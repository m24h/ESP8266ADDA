import os
import sys
import gc
import traceback
import datetime
from threading import Thread, Lock
import asyncio
from queue import Queue
from array import array
import tkinter as tk
import tkinter.messagebox
import tkinter.filedialog
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from binascii import a2b_base64	

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) 
import arpc
import conf

#application ID
app_id='CyclicVoltammetry.'+str(os.path.getmtime(__file__))
#calibration data, will be get from board when connected
calibration=None
# parameters for current data, non '_' started item will be exported when 'SAVE'
data_params={}
# data : list of {v:[], I:[], *}
data=[]
# for communication from working thread to tkinter window
queue=Queue()
#working thread, when it is set None, working thread should stop its work, it should be modified only by win main process
thread=None
#event loop for run main asyncio process
aloop=asyncio.new_event_loop()
# main process task, set by task itself, others should not modify it 
task=None
# help
help_text='''
1. This will output <start volt>, then sweep to <1st volt>, etc. and record voltage and current sampled.
2. The potential of the reference electrode will not be calculated by this software.
3. Because there is no potentiostat circuit, the actual scan voltage is different from the output voltage
4. Due to accuracy of Timer, DAC and ADC, the control parameters are maybe refined.
5. The output sweep rate (V/s) can be calculated by this formula : <volt step>/<sample interval> .
6. The actual potential sweep rate should be calculated from the results data.
7. IUPAC convention is adopted: The oxidation current is positive.
'''
#queue message defines
class QStart(object): 
	pass
class QProgress(int): # 0-100 means 0-100%
	pass
class QData(tuple): # ([] of v, [] of i)
	pass
class QMessage(tuple): # (str, *)
	pass

# main window
win = tk.Tk()
win.geometry('+%d+100'%((win.winfo_screenwidth()-900)/2))
win.title('Cyclic Voltammetry')

# control frame
params={
	'output_volt':(tk.DoubleVar(value=0.0), tk.DoubleVar(value=-1.0), tk.DoubleVar(value=1.0), tk.DoubleVar(value=0.0)),
	'volt_step':tk.DoubleVar(value=0.01),
	'sample_interval':tk.DoubleVar(value=1),
	'repeat_times':tk.IntVar(value=1),
	'init_time':tk.DoubleVar(value=1.0),
	'sensor_R':tk.DoubleVar(value=100.0)
}
validate_float=win.register(lambda v: (v:=v.replace('.','',1))=='' or (v:=v[1:] if v[0]=='-' else v)=='' or v.isdigit())
validate_int=win.register(lambda v:  v=='' or (v:=v[1:] if v[0]=='-' else v)=='' or v.isdigit())
control_bar=tk.Frame(win)
control_bar.pack(side='left', anchor='n', fill='y', expand=True)
tk.Label(control_bar, text='start volt (-5~5V) :').pack(side='top', anchor='w')
tk.Entry(control_bar, textvariable=params['output_volt'][0], validate='key', validatecommand=(validate_float,'%P')).pack(side='top', anchor='w', fill='x', pady=(0, 5))
tk.Label(control_bar, text='1st volt (-5~5V) :').pack(side='top', anchor='w')
tk.Entry(control_bar, textvariable=params['output_volt'][1], validate='key', validatecommand=(validate_float,'%P')).pack(side='top', anchor='w', fill='x', pady=(0, 5))
tk.Label(control_bar, text='2nd volt (-5~5V) :').pack(side='top', anchor='w')
tk.Entry(control_bar, textvariable=params['output_volt'][2], validate='key', validatecommand=(validate_float,'%P')).pack(side='top', anchor='w', fill='x', pady=(0, 5))
tk.Label(control_bar, text='3rd volt (-5~5V) :').pack(side='top', anchor='w')
tk.Entry(control_bar, textvariable=params['output_volt'][3], validate='key', validatecommand=(validate_float,'%P')).pack(side='top', anchor='w', fill='x', pady=(0, 5))
tk.Label(control_bar, text='volt step (0.001~5V):').pack(side='top', anchor='w')
tk.Entry(control_bar, textvariable=params['volt_step'], validate='key', validatecommand=(validate_float,'%P')).pack(side='top', anchor='w', fill='x', pady=(0, 5))
tk.Label(control_bar, text='sample interval (0.05~32s) :').pack(side='top', anchor='w')
tk.Entry(control_bar, textvariable=params['sample_interval'], validate='key', validatecommand=(validate_float,'%P')).pack(side='top', anchor='w', fill='x', pady=(0, 5))
tk.Label(control_bar, text='repeat times (1~?) :').pack(side='top', anchor='w')
tk.Entry(control_bar, textvariable=params['repeat_times'], validate='key', validatecommand=(validate_int,'%P')).pack(side='top', anchor='w', fill='x', pady=(0, 5))
tk.Label(control_bar, text='init time (0~?s) :').pack(side='top', anchor='w')
tk.Entry(control_bar, textvariable=params['init_time'], validate='key', validatecommand=(validate_float,'%P')).pack(side='top', anchor='w', fill='x', pady=(0, 5))
tk.Label(control_bar, text='sensor R (0.001~?Ω) :').pack(side='top', anchor='w')
tk.Entry(control_bar, textvariable=params['sensor_R'], validate='key', validatecommand=(validate_float,'%P')).pack(side='top', anchor='w', fill='x', pady=(0, 5))
btn_run=tk.Button(control_bar, text="RUN", command=lambda : run())
btn_run.pack(side='top', anchor='s', fill='x', pady=(5, 5))
tkvar_msg=tk.StringVar(value='')
tk.Label(control_bar, textvariable=tkvar_msg, anchor='nw', justify='left', wraplength=150).pack(side='top', anchor='nw', fill='y', expand=True)

# matplotlib plot in tk
def on_scroll(evt):
	if thread is None and (axe:=evt.inaxes) is not None and (evt.button=='up' or evt.button=='down'):
		xmin,xmax=axe.get_xlim()
		ymin,ymax=axe.get_ylim()
		scale=0.9 if evt.button=='up' else 1.1
		axe.set(xlim=(evt.xdata-(evt.xdata-xmin)*scale, evt.xdata+(xmax-evt.xdata)*scale),
		            ylim=(evt.ydata-(evt.ydata-ymin)*scale, evt.ydata+(ymax-evt.ydata)*scale))
		evt.canvas.draw_idle()
figure=Figure(figsize=(6, 5), dpi=100)
figure.subplots_adjust(left=0.2, top=0.95, right=0.95)
canvas=FigureCanvasTkAgg(figure, master=win)
canvas.get_tk_widget().pack(side='top', anchor='ne', fill='x', expand=True)
canvas.mpl_connect('scroll_event', on_scroll)
navbar=NavigationToolbar2Tk(canvas, win, pack_toolbar=False)
tk.Frame(navbar, bd=1, width=2, highlightthickness=0, relief='groove').pack(side='left', anchor='w', padx=5, pady=3, fill='y')
btn_save=tk.Button(navbar, text="SAVE DATA", state="disabled", command=lambda : save())
btn_save.pack(side='left')
tk.Button(navbar, text="HELP", command=lambda : help()).pack(side='left')
navbar.update()
navbar.pack(side='bottom', anchor='nw', fill='x')
axe1=figure.add_subplot(111) # only 1 plot in 1x1 table now

#when 'help' button clicked
def help():
	dlg=tk.Toplevel(win)
	wingeo=win.geometry().split('+')
	dlg.geometry('+'+str(int(wingeo[1])+50)+'+'+str(int(wingeo[2])+50))
	dlg.grab_set()
	dlg.title('Help')
	dlg._img=tk.PhotoImage(file=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'help.gif'))
	tk.Label(dlg, image=dlg._img, bg='white').pack(side='top', fill='x')
	tk.Label(dlg, justify='left', bg='white', text=help_text).pack(side='top', anchor='nw', ipadx=5, fill='x')
	
#when 'save' button clicked
def save():
	try:
		if (file:=tk.filedialog.asksaveasfile(title='Save Data File', defaultextension='.csv', filetypes=[('CSV','*.csv'),('All files','*')])):
			with file:
				keys=tuple(k for k in data_params.keys() if isinstance(k,str) and not k.startswith('_'))
				if keys:
					file.write(','.join(keys)+'\n')
					file.write(','.join(str(data_params[k]) for k in keys)+'\n')
					file.write('\n')
				for d in data:
					file.write('index,voltage,current\n')
					v=d['v']
					i=d['i']
					for idx in range(min(len(v), len(i))):
						file.write(str(idx)+','+str(v[idx])+','+str(i[idx])+'\n')
					file.write('\n')
	except PermissionError as e:
		tk.messagebox.showerror(title='Error', message=e)

#when 'run' button clicked
def run():
	global thread
	if thread is None:
		axe1.cla()
		data_params.clear()
		data.clear()
		axe1.set_xlabel("Voltage (V)")
		axe1.set_ylabel("Current (mA)")
		axe1.grid(True)
		canvas.draw()
		try:
			#reorganize parameters and calculate internal variables
			output_volt=tuple(t.get() for t in params['output_volt'])
			output_volt=tuple(-5 if t<-5 else 5 if t>5 else t for t in output_volt)
			output_volt=tuple((int(t/calibration['vref']/2*4096) if t>0 else -int(-t/calibration['vref']/2*4096)) for t in output_volt)
			for t in range(len(output_volt)):
				v=output_volt[t]*calibration['vref']*2/4096
				params['output_volt'][t].set(v)
				data_params['volt '+str(t)]=v
			volt_step=max(1,int(abs(params['volt_step'].get())/calibration['vref']/2*4096))
			t=volt_step*calibration['vref']*2/4096
			params['volt_step'].set(t)
			data_params['volt step']=t
			sample_interval=min(32767,max(25, int(params['sample_interval'].get()*1000/2)))  # split to 2 sample intervals in ms : voltage and current interlaced
			t=sample_interval*2/1000
			params['sample_interval'].set(t)
			data_params['sample interval']=t
			repeat_times=max(1, int(params['repeat_times'].get()))
			params['repeat_times'].set(repeat_times)
			data_params['repeat times']=repeat_times
			init_time=max(0, int(params['init_time'].get()*1000))
			t=init_time/1000
			params['init_time'].set(t)
			data_params['init time']=t
			sensor_R=max(0.001, params['sensor_R'].get())
			params['sensor_R'].set(sensor_R)
			data_params['sensor R']=sensor_R
			data_params['date time']=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")			
			#start another thread to process
			thread=Thread(target=aloop.run_until_complete, args=(proc(output_volt, volt_step, sample_interval, repeat_times, init_time, sensor_R),))
			thread.start()
			btn_run['text']='STOP'
			btn_run['command']=stop
			btn_save['state']='disabled'
		except Exception as e:
			thread=None
			tk.messagebox.showerror(title='Error', message=e)
			traceback.print_exc()
			return
	win.after(250, wait)

#colors for different scan
line_colors=('red','magenta','orange','yellow','green','cyan','blue')
# wait for complete and render the canvas
def wait():
	global thread
	if thread is not None and not thread.is_alive():
		thread=None
	have_new_data=False
	while not queue.empty():
		msg=queue.get()
		if isinstance(msg, arpc.RemoteException):
			tk.messagebox.showerror(title='Error', message='remote: '+str(msg))
		elif isinstance(msg, BaseException):
			tk.messagebox.showerror(title='Error', message=msg)
		elif isinstance(msg, QStart):
			data.append({'v':[], 'i': [], '_color': (0 if not data else (data[-1]['_color']+1)%len(line_colors)) , '_lastdraw':None})
		elif isinstance(msg, QData):
			data[-1]['v'].extend(msg[0])
			data[-1]['i'].extend(msg[1])
			have_new_data=True
		elif isinstance(msg, QProgress):
			btn_run['text']='STOP ('+str(msg)+' %)'
		elif isinstance(msg, QMessage):
			tkvar_msg.set(' '.join(str(s) for s in msg))
	if have_new_data:
		if data[-1]['_lastdraw'] is not None: #delete old drawing in same scan cycle
			data[-1]['_lastdraw'].remove()
		data[-1]['_lastdraw'], =axe1.plot(data[-1]['v'], data[-1]['i'], color=line_colors[data[-1]['_color']])
		canvas.draw_idle()
	if thread is not None:
		win.after(250, wait)
	else:
		done()

# complete
def done():
	btn_run['text']='RUN'
	btn_run['command']=run
	btn_save['state']='normal' if len(data)>0 else 'disabled'
	navbar.update()
	navbar.push_current() #save default view for best zoom position
	
# abort and stop
async def _stop():
	async with await arpc.connect(conf.server['host'], conf.server['port'], password=conf.server['password']) as remote:
		await remote.abort()
def stop():
	asyncio.run(_stop())

# process at local end
async def proc(output_volt, volt_step, sample_interval, repeat_times, init_time, sensor_R):
	global task
	task=asyncio.current_task()
	
	#prepare process variables
	points_total=repeat_times*(sum(max(1,(abs(output_volt[t]-output_volt[t-1])+volt_step-1)//volt_step) for t in range(1,len(output_volt)))+1)
	points_done=0
	scale_v=-6.144/32768*calibration['adc'][0][1] # 6.144V measure range
	offset_v=-calibration['adc'][0][0]
	scale_i=0.256/32768/sensor_R*1000*calibration['adc'][5][1] # 0.256V measure range, result in mA
	offset_i=calibration['adc'][5][0]
	swapbytes=(sys.byteorder=='little')  #local endian is different
	
	#local RPC
	rpc=arpc.RPC()
	@rpc()
	def message(*args):
		queue.put(QMessage(args))
	@rpc()
	def rawdata(v, i):  #raw data is base64 encoded, array of 2 bytes unsigned int in BIG endian
		av=array('h')
		av.frombytes(a2b_base64(v))
		ai=array('h')
		ai.frombytes(a2b_base64(i))
		nonlocal points_done
		points_done=points_done+len(av)
		if swapbytes: 
			av.byteswap()
			ai.byteswap()
		queue.put(QData((tuple((x*scale_v+offset_v) for x in av), tuple((x*scale_i+offset_i) for x in ai))))
		queue.put(QProgress(points_done*100//points_total))

	#main process
	try:
		async with await arpc.connect(conf.server['host'], conf.server['port'], rpc=rpc, password=conf.server['password']) as remote:
			await remote.io2(0) # lighten LED
			while thread is not None and (repeat_times:=repeat_times-1)>=0:
				queue.put(QStart())
				await remote.proc(output_volt, volt_step, sample_interval, init_time)
			await remote.io2(1) # turn off LED
	except BaseException as e:
		queue.put(e)

	task=None
	gc.collect()

# upload remote program
async def upload():
	async with await arpc.connect(conf.server['host'], conf.server['port'], password=conf.server['password']) as remote:
		global calibration
		calibration=await remote.calibration()
		try:
			if app_id==await remote.app_id():
				print('Same program exists, no need to upload')
				return
		except:
			pass
		await remote.exec('rpc["app_id"]='+repr(app_id))
		await remote.exec('''
from init import io2
import machine
import gc
import micropython
gc.collect()
machine.freq(160000000)
io2.init(mode=machine.Pin.OPEN_DRAIN, value=1)
buf16=bytearray(2)
ring_len=micropython.const(1024)
ring_v=bytearray(ring_len)
view_v=memoryview(ring_v)
ring_i=bytearray(ring_len)
view_i=memoryview(ring_i)
timer=machine.Timer(-1)
''')
		await remote.exec('''
def dac(v): 
	from init import ldac,fs,spi
	from __main__ import buf16
	a=2048+v//2
	b=a-v
	a=a | 0x1000
	b=b | 0x5000
	buf16[0]=a>>8
	buf16[1]=a
	fs(0)
	spi.write(buf16)
	fs(1)
	buf16[0]=b>>8
	buf16[1]=b
	fs(0)
	spi.write(buf16)
	fs(1)
	ldac(0)
	ldac(1)
''')
		await remote.exec('''
class Sample:
	pass
def _init(self, output_volt, volt_step, sample_interval):
	self.volt=output_volt
	self.volt_idx=0
	self.volt_cur=output_volt[0]
	self.step=volt_step
	self.volt_len=len(output_volt)
	if self.volt_len<2 or output_volt[1]==output_volt[0]:
		self.step_cnt=0
	elif output_volt[1]>output_volt[0]:
		self.step_cur=volt_step
		self.step_cnt=(output_volt[1]-output_volt[0]-1)//volt_step
	else:
		self.step_cur=-volt_step
		self.step_cnt=(output_volt[0]-output_volt[1]-1)//volt_step
	self.cmd_si=b'\\x9E\\xE3' if sample_interval<10 else b'\\x9E\\xC3' if sample_interval<20 else b'\\x9E\\xA3' if sample_interval<40 else b'\\x9E\\x83'
	self.cmd_sv=b'\\xA0\\xE3' if sample_interval<10 else b'\\xA0\\xC3' if sample_interval<20 else b'\\xA0\\xA3' if sample_interval<40 else b'\\xA0\\x83'
	self.ring_w=0
	self.ch_v=True 
	self.done=False
Sample.init=_init
''')
		await remote.exec('''		
def _ontimer(self, tim):
	try:
		from init import i2c, ADC_ADDR
		from __main__ import dac, ring_v, ring_i, ring_len, buf16
		if self.done:
			tim.deinit()
		elif self.ch_v:
			self.ch_v=False
			t=self.ring_w
			i2c.readfrom_mem_into(ADC_ADDR, 0, buf16)
			i2c.writeto_mem(ADC_ADDR, 1, self.cmd_si)
			ring_v[t]=buf16[0]
			ring_v[t+1]=buf16[1]
		else:
			self.ch_v=True
			t=self.ring_w
			i2c.readfrom_mem_into(ADC_ADDR, 0, buf16)
			i2c.writeto_mem(ADC_ADDR, 1, self.cmd_sv)
			ring_i[t]=buf16[0]
			ring_i[t+1]=buf16[1]
			self.ring_w=(t+2)%ring_len
			if (t:=self.step_cnt)>0:
				self.step_cnt=t-1
				v=self.volt_cur+self.step_cur
				dac(v)
				self.volt_cur=v
			elif (t:=self.volt_idx+1)>=self.volt_len:
				self.done=True
			else:
				v=self.volt[t]
				dac(v)
				self.volt_cur=v
				self.volt_idx=t
				if t+1>=self.volt_len or (v2:=self.volt[t+1])==v:
					self.step_cur=0
					self.step_cnt=0
				elif v2>v:
					t=self.step
					self.step_cur=t
					self.step_cnt=(v2-v-1)//t
				else:
					t=self.step
					self.step_cur=-t
					self.step_cnt=(v-v2-1)//t
	except:
		self.done=True
		raise
Sample.ontimer=_ontimer
sample=Sample()
''')
		await remote.exec('''
@rpc('proc')
async def proc(output_volt, volt_step, sample_interval, init_time):
	import gc
	import uasyncio as asyncio
	import arpc
	from __main__ import sample, timer, dac, ring_len, view_v, view_i
	from machine import Timer, disable_irq, enable_irq
	from binascii import b2a_base64	
	from init import i2c, ADC_ADDR

	gc.collect()
	dac(output_volt[0])
	sample.init(output_volt, volt_step, sample_interval)
	i2c.writeto_mem(ADC_ADDR, 1, sample.cmd_sv)
	await asyncio.sleep_ms(sample_interval+init_time)
	timer.init(mode=Timer.PERIODIC, period=sample_interval, callback=sample.ontimer)
	try:
		ring_r=0
		remote=arpc.session()
		while not sample.done or ring_r!=sample.ring_w:
			await asyncio.sleep_ms(250)
			irqstate=disable_irq()
			ring_w=sample.ring_w
			enable_irq(irqstate)
			if ring_r>ring_w:
				while ring_r<ring_len:
					end=min(ring_r+256, ring_len)
					await remote.rawdata(b2a_base64(view_v[ring_r:end]), b2a_base64(view_i[ring_r:end]), __arpc_toss=True)
					ring_r=end
				ring_r=0
			while ring_r<ring_w:
				end=min(ring_r+256, ring_w)
				await remote.rawdata(b2a_base64(view_v[ring_r:end]), b2a_base64(view_i[ring_r:end]), __arpc_toss=True)
				ring_r=end
	finally:
		timer.deinit()
		gc.collect()
''')
		await remote.exec('''
import gc
@rpc('abort')
def abort():
	from __main__ import sample
	sample.done=True
gc.collect()
''')


if __name__=='__main__':
	print('Uploading remote program ...')
	asyncio.run(upload())
	print('Done')
	win.mainloop()
	if task is not None:
		aloop.call_soon_threadsafe(task.cancel)

