class AHT20:
    """AHT20 Temperature and Humidity Sensor Driver"""
    def __init__(self, i2c, address=0x38):
        self.i2c = i2c
        self.address = address
        utime.sleep_ms(40)
        self._init_sensor()
    
    def _init_sensor(self):
        try:
            self.i2c.writeto(self.address, bytearray([0xBE, 0x08, 0x00]))
            utime.sleep_ms(10)
        except Exception as e:
            log_error(f"AHT20 init error: {e}")
    
    def read_data(self):
        try:
            # Trigger measurement
            self.i2c.writeto(self.address, bytearray([0xAC, 0x33, 0x00]))
            utime.sleep_ms(80)
            
            # Read 6 bytes
            data = self.i2c.readfrom(self.address, 6)
            
            if data[0] & 0x80:
                return None, None
            
            # Extract humidity (20 bits)
            humidity_raw = ((data[1] << 16) | (data[2] << 8) | data[3]) >> 4
            humidity = (humidity_raw / 1048576.0) * 100
            
            # Extract temperature (20 bits)
            temp_raw = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]
            temperature = (temp_raw / 1048576.0) * 200 - 50
            
            return temperature, humidity
        except Exception as e:
            log_error(f"AHT20 read error: {e}")
            return None, None