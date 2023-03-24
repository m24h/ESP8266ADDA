from machine import Pin,ADC,SPI,SoftI2C
from micropython import const

adc0 = ADC(0)
io0=Pin(0, mode=Pin.OPEN_DRAIN, pull=Pin.PULL_UP, value=1)
io2=Pin(2, mode=Pin.OPEN_DRAIN, pull=Pin.PULL_UP, value=1)
i2c=SoftI2C(scl=Pin(4, mode=Pin.OPEN_DRAIN, pull=Pin.PULL_UP, value=1), sda=Pin(5, mode=Pin.OPEN_DRAIN, pull=Pin.PULL_UP, value=1), freq=100000)
ADC_ADDR=const(0x48)
spi=SPI(1, baudrate=2000000, polarity=1, phase=0)
fs=Pin(15, mode=Pin.OUT, value=1)
ldac=Pin(16, mode=Pin.OUT, value=1)
rdy=Pin(12, mode=Pin.IN, pull=Pin.PULL_UP)
