# ESP8266ADDA
A board that integrates ESP8266, TLV5614, and ADS1115, provides usage using arpc (Asynchronous RPC)

## Features:

* ESP8266 with WIFI for remote control.
* 4 channel single/differential 16bit ADC ADS1115/SGM58031 with rail to rail input/common voltage scope.
* 4 channel 12 bit TLV5614/MS5614.
* Using AD8532 as driver for high currents output (with protection circuits).
* 2 GPIO (IO0 and IO2), 1 ADC of ESP8266 (10bit 0-1V), 1 USART are available.
* Using Type-C USB as power supply port
* Using RPC framework for flexible remote programmable, on-site uploading python applications
* 3 demo python applications : Sampler, Calibration and CyclicVoltammetry
* Many small scripts :  call.py eval.py exec.py info.py reset.py sample.py stop.py

## Issues

* Micropython/ESP8266 sometimes stops the network response without any trace information, reset is the only way out.
* There is neither hard timer nor hard IRQ in Micropython/ESP8266, applications are somtimes limited.
