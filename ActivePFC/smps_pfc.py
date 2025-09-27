"""
This Code was created by Claudio Cabete with assistance from Grok. May 2025
Code for Single Stage PFC/SMPS using Zero_Cross detection as a way to lock in with AC sine wave,
It uses LUT for dynamimc pulse width.
It uses Feedback loop to apply a bias to the width from the LUT
https://homatica.cc/projects/pfc-controller
"""

from machine import Pin, PWM, ADC, Timer, freq
import array
import math
import utime
import micropython

# Overclock
#freq(150000000)
print(freq())

# Enable emergency exception buffer
micropython.alloc_emergency_exception_buf(100)

# Parameters
pwm_resolution = 65535  # 16-bit PWM
init_pwm_duty = 0
pwm_freq = 80000
pfc_timer_pwm_freq = 12022
avg_period = 0
duty_ = 0
duty_scale = 100
scaled_duty = 0
voltage = 0
avg_voltage = 0
lut_index = 0
last_zc_time = 0
last_zc_valid = 0

zc_samples = 4
zc_index = 0
zc_periods = array.array('f', [0] * zc_samples)  # Pre-allocate

fbl_samples = 4
fbl_index = 0
fbl_readings = array.array('f', [0] * fbl_samples)

# Generate LUT as array.array('H')
min_duty = 0.3  # 30%
max_duty = 0.8  # 80%
abs_max_duty = 52767 # Absolut Maximum Pulse width Value 
abs_min_duty = 0 # Absolut Minimum Pulse width Value
lut_items = 200  # Full cycle (16.667 ms)
lut_items_half = int(lut_items / 2) # Half-cycle (8.333 ms)
lut = array.array('H', [0] * lut_items)
phase_offset =42  # ~64.8° (2.99 ms) delay, adjust as needed


# Generate 100-item LUT with phase shift
lut_half = array.array('H', [0] * lut_items_half)
for n in range(lut_items_half):
    theta = n * math.pi / lut_items_half
    duty = min_duty + (max_duty - min_duty) * (1 - math.sin(theta))
    duty_u16 = int(duty * pwm_resolution)
    lut_half[n] = duty_u16

# Append two identical 100-item LUTs
for n in range(lut_items_half):
    lut[n] = lut_half[n]  # First half-cycle (0 to π)
    lut[n + lut_items_half] = lut_half[n]  # Second half-cycle (π to 2π)
    
print(lut)

# Feedback loop setup (GP26/ADC0 for feedback)
fbl = ADC(26)  # 0–3.3V input

# PFC Sampling timing signal PWM;
pfc_timer_pwm = PWM(Pin(19))
pfc_timer_pwm.freq(pfc_timer_pwm_freq)  
pfc_timer_pwm.duty_u16(32767) 

# PFC Sense Pin
pfc_sense_pin = Pin(20, Pin.IN, Pin.PULL_DOWN)

# Zero Cross pin
zc_pin = Pin(22, Pin.IN, Pin.PULL_UP)

# Trigger pin for scope (GP2)
debug_pin = Pin(16, Pin.OUT)
debug_pin.value(0)  # Initialize low


# PWM setup (GP0, 50 kHz)
pwm = PWM(Pin(21))
pwm.freq(pwm_freq)
pwm.duty_u16(init_pwm_duty)  # Start with 0% duty (LIN high, LO low, MOSFET off)


# Feed back loop for dynamic duty_scale
          
target_voltage = 1.525  # Target feedback voltage
k_p = 100  # Proportional gain (tune this)
def fbl_callback(pin):
    global avg_voltage, duty_scale, fbl_index, voltage, pwm_enabled
    if utime.ticks_diff(utime.ticks_us(), last_zc_time) > 16000:
        pwm_enabled = False
    
    voltage = fbl.read_u16() * 3.3 / 65535
    fbl_readings[fbl_index] = voltage
    fbl_index = (fbl_index + 1) % fbl_samples
    avg_voltage = sum(fbl_readings) / fbl_samples  # Update every sample
    error = target_voltage - avg_voltage
    duty_scale += int(k_p * error)  # Proportional adjustment
    
    if duty_scale < 500:
        duty_scale = 500

    if duty_scale > 1000:
        duty_scale = 1000         

  

# PWM update callback with feedback
lt_tick=0
duty_ = 0
@micropython.viper
def update_pwm(pin: object):
    global lt_tick, lut_index, scaled_duty, duty_
    c_tick = utime.ticks_us()
    idx: int = int(lut_index)  # Explicit cast
    
    """
    if idx == 0:
        debug_pin.value(1)
    elif idx == 1:
        debug_pin.value(0)
        
    
    if idx == 48:
        debug_pin.value(1)
    elif idx == 49:
        debug_pin.value(0)
    """
        
   
    duty: int = int(lut[idx])  # Native u16 access
    duty_ = duty
    scale: int = int(duty_scale)  # Fixed-point: 500–1500
    scaled_duty: int = duty * scale // 1000  # Fixed-point division

    if scaled_duty > abs_max_duty:
        scaled_duty = abs_max_duty
    elif scaled_duty < abs_min_duty:
        scaled_duty = abs_min_duty

    pwm.duty_u16(int(scaled_duty))
    
    new_idx: int = (idx + 1) # Local computation
    
    if new_idx == 200:
        new_idx = 0
        
    lut_index = new_idx  # Assign to global
    lt_tick = utime.ticks_diff(utime.ticks_us(), c_tick)

 
# Zero-crossing interrupt zc_callback
zcross_tick= 0
pwm_enabled = False
last_zc_valid = utime.ticks_us()

# Zero-crossing callback
zcross_tick = 0
fbl_timer = Timer()
def zc_callback(pin):
    global last_zc_time, avg_period, lut_index, last_zc_valid, zc_index, pwm_enabled, zcross_tick
    current_time = utime.ticks_us()
    zcross_tick = utime.ticks_diff(current_time, last_zc_valid)
    if zcross_tick > 2000:  # 2 ms debounce
        fbl_timer.init(period=5, mode=Timer.ONE_SHOT, callback=fbl_callback)
        lut_index = phase_offset  # Apply phase shift
        pwm_enabled = True
        if last_zc_time != 0:
            period = utime.ticks_diff(current_time, last_zc_time) / 1000
            zc_periods[zc_index] = period
            zc_index = (zc_index + 1) % zc_samples
            if zc_index == 0:
                avg_period = sum(zc_periods) / zc_samples
        last_zc_valid = current_time
    last_zc_time = current_time

# Initialize interrupts
zc_pin.irq(trigger=Pin.IRQ_RISING, handler=zc_callback)
pfc_sense_pin.irq(trigger=Pin.IRQ_RISING, handler=update_pwm)


# Main loop for debugging
"""
try:
    while True:
        if lut_index in (0,1,99):
            print(f"lt_tick: {lt_tick}, avg_period:{avg_period:.2f}, avg_voltage:{avg_voltage:.3f}, lut_index:{lut_index}, duty:{duty_}, duty_scale:{duty_scale}, scaled_duty:{scaled_duty}")
            utime.sleep_ms(5000)
except KeyboardInterrupt:
    pwm.deinit()
    debug_pin.value(0)
    print("Stopped")
"""
