"""
AHT20 Temperature and Humidity Sensor Driver for MicroPython
Save this file as: lib/aht20.py
"""

import utime


class AHT20:
    """AHT20 Temperature and Humidity Sensor Driver"""
    
    def __init__(self, i2c, address=0x38):
        """
        Initialize AHT20 sensor
        
        Args:
            i2c: I2C bus object
            address: I2C address (default 0x38)
        """
        self.i2c = i2c
        self.address = address
        utime.sleep_ms(100)  # Wait for sensor power-up
        self._init_sensor()
    
    def _init_sensor(self):
        """Send initialization command to sensor with retries"""
        # Try soft reset first
        try:
            self.i2c.writeto(self.address, bytearray([0xBA]))
            utime.sleep_ms(20)
        except:
            pass  # Ignore if reset fails
        
        # Wait longer after reset
        utime.sleep_ms(100)
        
        # Try initialization without reading status first
        for attempt in range(5):
            try:
                # Send calibration command (0xBE with params 0x08, 0x00)
                self.i2c.writeto(self.address, bytearray([0xBE, 0x08, 0x00]))
                utime.sleep_ms(20)  # Longer wait after init command
                print(f"AHT20 init command sent successfully on attempt {attempt + 1}")
                return  # Success
                
            except OSError as e:
                print(f"AHT20 init attempt {attempt + 1} failed: {e}")
                if attempt < 4:
                    utime.sleep_ms(100)
                else:
                    raise RuntimeError(f"AHT20 initialization failed after 5 attempts: {e}")
    
    def read_data(self):
        """
        Read temperature and humidity from sensor
        
        Returns:
            tuple: (temperature in °C, humidity in %)
                   Returns (None, None) if sensor is busy or read fails
        """
        try:
            # Trigger measurement (0xAC with params 0x33, 0x00)
            self.i2c.writeto(self.address, bytearray([0xAC, 0x33, 0x00]))
            utime.sleep_ms(80)  # Wait for measurement to complete
            
            # Read 6 bytes of data
            data = self.i2c.readfrom(self.address, 6)
            
            # Check if busy bit is clear (bit 7 of status byte)
            if data[0] & 0x80:
                print("Sensor busy, try again")
                return None, None
            
            # Extract humidity (20 bits)
            humidity_raw = ((data[1] << 16) | (data[2] << 8) | data[3]) >> 4
            humidity = (humidity_raw / 1048576.0) * 100
            
            # Extract temperature (20 bits)
            temp_raw = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]
            temperature = (temp_raw / 1048576.0) * 200 - 50
            
            return temperature, humidity
            
        except OSError as e:
            print(f"AHT20 read error: {e}")
            return None, None
    
    def soft_reset(self):
        """Soft reset the sensor"""
        try:
            self.i2c.writeto(self.address, bytearray([0xBA]))
            utime.sleep_ms(100)
            self._init_sensor()
        except Exception as e:
            raise RuntimeError(f"AHT20 reset failed: {e}")
