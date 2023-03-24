import arpc
import asyncio
import conf
import sys
def prn(f):
	f=f'{f:.20f}'
	f=f.rstrip('0')
	if f[-1]=='.' : f=f+'0'
	print (f)
async def read(mux, rng, interval, cnt):
	async with await arpc.connect(conf.server['host'], conf.server['port'], password=conf.server['password']) as sess:
		cali=await sess.calibration()
		factor=cali['adc'][rng][1]/32768*(6.144,4.096,2.048,1.024,0.512,0.256)[rng]
		offset=cali['adc'][rng][0]
		await sess.adc_config(mux, rng, 4)
		while True:
			prn ((await sess.adc_read())*factor+offset)
			if cnt>0:
				if (cnt:=cnt-1)==0: break
			await asyncio.sleep(interval)

if __name__=='__main__':
	if len(sys.argv)<4:
		print(f'''\
Usage:
	{sys.argv[0]} <mux> <range> <interval> [<count>]
	<mux>: 0-7 means IN0-IN1, IN0-IN3, IN1-IN3, IN2-IN3, IN0-GND, IN1-GND, IN2-GND, IN3-GND
	<range>: 0-5 means 6.144V, 4.096V, 2.048V, 1.024V, 0.512V, 0.256V
	<interval>: seconds between sample points
	<count>: total points to sample, 0 (if not specified) means non stop''')
	else:
		try:
			asyncio.run(read(int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), 0 if len(sys.argv)<5 else int(sys.argv[4])))
		except KeyboardInterrupt:
			pass