from __main__ import rpc
from init import *
import cali

rpc['hw_id']='ESP8266.MS5614.ADS1115.20230309'
rpc['help']='''hw_id(): return hardware information
adc0(): read raw value from esp8266 adc0 port
io0([v]): get/set value of io0, it's initialized as open-drain, pull-up, default 1
io2([v]): get/set value of io2, it's initialized as open-drain, pull-up, default 1
adc_read(): read raw value (-32768~32767) from ADC
adc_config(mux, pga, spd): config ADC
dac_write(ch, pwr, spd, val, load=True): set raw DAC output value, then it will take effect if <load> is True
dac_load(): make DAC output take effect
calibration(): read calibration data of this board
'''
@rpc('adc0')
def adc0_read():
    return adc0.read()

rpc['io0']=io0
rpc['io2']=io2

@rpc('adc_read')
def adc_read():
	r=int.from_bytes(i2c.readfrom_mem(ADC_ADDR, 0, 2), 'big')
	return r if r<32768 else r-65536

@rpc('adc_config')
def adc_config(mux, pga, spd):
	i2c.writeto_mem(ADC_ADDR, 1, int.to_bytes(((mux&0x07)<<12)|((pga&0x07)<<9)|((spd&0x07)<<5)|0x8003, 2, 'big'))

@rpc('dac_write')
def dac_write(ch, pwr, spd, val, load=True):
	val=(val&0x0fff)|((ch<<14)&0xc000)
	if spd:
		val=val|0x1000
	if pwr:
		val=val|0x2000
	fs(0)
	spi.write(int.to_bytes(val, 2, 'big'))
	fs(1)
	if load:
	    ldac(0)
	    ldac(1)

@rpc('dac_load')
def dac_load():
    ldac(0)
    ldac(1)

dac_write(0,0,0,0,False)
dac_write(1,0,0,0,False)
dac_write(2,0,0,0,False)
dac_write(3,0,0,0,False)
dac_load()

