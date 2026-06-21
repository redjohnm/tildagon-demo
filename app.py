import bluetooth
import struct
import time
import math
import json
import app
import imu  
from app_components import clear_background
from tildagonos import tildagonos
from system.eventbus import eventbus
from events.input import Buttons, BUTTON_TYPES

# BLE Constants
_IRQ_SCAN_RESULT = 5
_IRQ_SCAN_DONE = 6
SQUAD_APP_UUID = 0xAA55  

class SquadTrackerApp(app.App):
    def __init__(self):
        super().__init__()
        
        try: tildagonos.set_led_power(True)
        except Exception: pass
        
        self.squad_db = self.load_squad_data()
        self.squad_members = {}  # Format: {mac: (timestamp, rssi, broadcast_name)}
        
        self.tracking_mode = 0 
        self.selected_target_mac = None
        self.current_heading = 0.0
        self.last_gyro_update = time.ticks_ms()
        self.target_bearing = 0.0  
        self.status_msg = "Booting Radar..."

        self.button_states = Buttons(self)

        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self._ble_irq)
        self.start_broadcasting()
        self.start_scanning()

    def load_squad_data(self):
        try:
            with open("/apps/squad_tracker/squad.json", "r") as f:
                return json.load(f)
        except Exception:
            # Added "my_name" parameter to the local storage schema
            return {"my_name": "AnonHacker", "my_group": "Alpha Squad", "my_village": "Village 4", "friends": {}}

    def save_squad_data(self):
        try:
            with open("/apps/squad_tracker/squad.json", "w") as f:
                json.dump(self.squad_db, f)
            self.status_msg = "Saved to Squad List!"
        except Exception:
            self.status_msg = "Write Error!"

    def start_broadcasting(self):
        """Appends your custom username string directly into live radio waves."""
        my_name = self.squad_db.get("my_name", "Anon")
        # Enforce name limit constraints to fit inside the standard 31-byte BLE boundary
        name_bytes = my_name.encode('utf-8')[:12] 
        
        # Structure: 
        # 1. 2 bytes total header length + Type (0x03) + 16-bit App UUID
        # 2. Complete Local Name flag header (Type: 0x09) + raw encoded name bytes
        base_payload = struct.pack("<BBB", 2, 0x03, SQUAD_APP_UUID)
        name_payload = struct.pack("<BB", len(name_bytes) + 1, 0x09) + name_bytes
        
        full_payload = base_payload + name_payload
        try: 
            self.ble.gap_advertise(100000, full_payload)
        except Exception: 
            self.status_msg = "TX Payload Limit Error"

    def start_scanning(self):
        self.ble.gap_scan(0, 30000, 30000, True)

    def _ble_irq(self, event, data):
        if event == _IRQ_SCAN_RESULT:
            addr_type, addr, adv_type, rssi, adv_data = data
            
            uuid_bytes = struct.pack("<H", SQUAD_APP_UUID)
            search_token = bytes([0x03]) + uuid_bytes
            
            if search_token in bytes(adv_data):
                mac_key = ":".join("{:02x}".format(b) for b in addr)
                
                # --- LIVE USERNAME PACKET PARSER ---
                broadcast_name = mac_key[:8] # Default fallback name string
                adv_bytes = bytes(adv_data)
                
                # Search for the Local Name Flag block (0x09) inside payload string
                idx = 0
                while idx < len(adv_bytes):
                    length = adv_bytes[idx]
                    if length == 0: break
                    if idx + length >= len(adv_bytes): break
                    
                    block_type = adv_bytes[idx + 1]
                    if block_type == 0x09: # Match local name type token flag
                        try:
                            # Extract string slice safely
                            broadcast_name = adv_bytes[idx + 2 : idx + 1 + length].decode('utf-8')
                        except Exception:
                            pass
                        break
                    idx += length + 1
                
                is_friend = mac_key in self.squad_db["friends"]
                friend_info = self.squad_db["friends"].get(mac_key, {})
                
                if self.tracking_mode == 1:
                    if not is_friend or friend_info.get("group") != self.squad_db["my_group"]:
                        return
                elif self.tracking_mode == 2:
                    if mac_key != self.selected_target_mac:
                        return
                
                # If they are already a whitelisted friend, prefer your customized name
                display_name = friend_info.get("name", broadcast_name)
                self.squad_members[mac_key] = (time.time(), rssi, display_name)
                
        elif event == _IRQ_SCAN_DONE:
            self.start_scanning()

    def capture_nearest_peer(self):
        if not self.squad_members:
            self.status_msg = "No one in range!"
            return

        closest_mac = max(self.squad_members, key=lambda k: self.squad_members[k][1])
        _, rssi, captured_name = self.squad_members[closest_mac]

        if rssi < -60:
            self.status_msg = "Get closer to pair!"
            return

        # Instead of generic IDs, save them under their self-broadcasted nickname!
        self.squad_db["friends"][closest_mac] = {
            "name": captured_name,
            "group": self.squad_db["my_group"],
            "village": self.squad_db["my_village"]
        }
        self.save_squad_data()

    def update_heading_from_gyro(self):
        try:
            _, _, gyro_z = imu.gyro_read()
            now_ms = time.ticks_ms()
            dt = time.ticks_diff(now_ms, self.last_gyro_update) / 1000.0
            self.last_gyro_update = now_ms
            if abs(gyro_z) > 0.05:
                self.current_heading += math.degrees(gyro_z * dt)
                self.current_heading %= 360
        except Exception: pass

    def update_led_ring(self, relative_angle, rssi):
        for i in range(1, 13): tildagonos.leds[i] = (0, 0, 0)
        if rssi > -65:    color = (0, 255, 0)    
        elif rssi > -85:  color = (255, 120, 0)  
        else:             color = (255, 0, 0)    
        led_index = int(((relative_angle + 15) % 360) / 30) + 1
        if 1 <= led_index <= 12: tildagonos.leds[led_index] = color
        tildagonos.leds.write()

    def update(self, delta):
        now = time.time()
        self.update_heading_from_gyro()
        
        if self.button_states.get(BUTTON_TYPES["CONFIRM"]):
            self.button_states.clear()  
            self.tracking_mode = (self.tracking_mode + 1) % 3
            self.squad_members.clear()  

        if self.button_states.get(BUTTON_TYPES["UP"]):
            self.button_states.clear()
            self.capture_nearest_peer()

        for mac, (last_seen, rssi, name) in list(self.squad_members.items()):
            if now - last_seen > 10: del self.squad_members[mac]
                
        squad_count = len(self.squad_members)
        if squad_count > 0:
            strongest_mac = max(self.squad_members, key=lambda k: self.squad_members[k][1])
            _, strongest_rssi, friend_name = self.squad_members[strongest_mac]
            
            if self.tracking_mode == 2 or self.selected_target_mac is None:
                self.selected_target_mac = strongest_mac
            
            relative_angle = (self.target_bearing - self.current_heading) % 360
            
            if strongest_mac in self.squad_db["friends"]:
                f_info = self.squad_db["friends"][strongest_mac]
                self.status_msg = f"{f_info['name']} ({f_info['village']})"
            else:
                # Even if they aren't whitelisted yet, show their over-the-air name!
                self.status_msg = f"Seen: {friend_name}"
                
            self.update_led_ring(relative_angle, strongest_rssi)
        else:
            self.status_msg = "Scanning..."
            pulse = int((math.sin(time.time() * 4) + 1) * 40)
            for i in range(1, 13): tildagonos.leds[i] = (0, 0, 0)
            tildagonos.leds[1] = (0, 0, pulse)
            tildagonos.leds.write()

    def draw(self, ctx):
        clear_background(ctx)
        ctx.font_size = 16
        ctx.text_align = ctx.CENTER
        
        ctx.rgb(100, 150, 255)
        ctx.move_to(0, -45)
        if self.tracking_mode == 0: ctx.text("[MODE: SEARCH ALL]")
        elif self.tracking_mode == 1: ctx.text(f"[GROUP: {self.squad_db['my_group']}]")
        elif self.tracking_mode == 2: ctx.text("[MODE: LOCK SOLO]")

        ctx.rgb(255, 255, 255)
        ctx.move_to(0, -15)
        ctx.text("SQUAD-TRACKER")
        
        ctx.rgb(0, 255, 130)
        ctx.move_to(0, 15)
        ctx.text(self.status_msg)
        
        ctx.rgb(130, 130, 130)
        ctx.move_to(0, 45)
        ctx.font_size = 12
        ctx.text("[Press UP to capture name]")

__app_export__ = SquadTrackerApp
