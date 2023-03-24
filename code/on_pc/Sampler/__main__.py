import os
import sys
import gc
import math
import traceback
import datetime
from collections import deque
from threading import Thread
import asyncio
from queue import Queue
from array import array
from binascii import a2b_base64	
import tkinter as tk
from tkinter import ttk 
import tkinter.messagebox
import tkinter.filedialog
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib import pyplot
import numpy
from scipy import signal, fft

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) 
import arpc
import conf

#application ID
app_id='Sampler.'+str(os.path.getmtime(__file__))
# main window
win = tk.Tk()
#calibration data, will be get from board when connected
calibration=None
# parameters for current data, non '_' started item will be exported when 'SAVE'
data_params={}
# data : deque of sampled data
data=None
data_plot=deque([], maxlen=256)
# for communication from working thread to tkinter window
queue=Queue()
#working thread, when it is set None, working thread should stop its work, it should be modified only by win main process
thread=None
#event loop for run main asyncio process
aloop=asyncio.new_event_loop()
# main process task, set by task itself, others should not modify it 
task=None
# help text
help_text='''
Set the parameters of sampling, then click 'RUN' to start.
After that, data points can be trimmed and filtered.
For the reason that there is no hard IRQ and timer in Micropython/ESP8266,
sample rate is limited below 32 sps (25 sps for SGM58031) .
In sampling duration, only the last 256 points is drawed.
After sampling, the number of the last points keeped is specified by 'Buffer size'
If 'Non stop' is not checked, it will stop automatically when 'Buffer size' points is sampled.
'''
q_new_data='have new data'
class QMessage(tuple): # (str, *)
	pass

# control frame
params_chooses={
	'buffer_size' : ('256', '512', '1024', '4096'),
	'channel' : ('IN0-IN1', 'IN0-IN3', 'IN1-IN3', 'IN2-IN3', 'IN0-GND', 'IN1-GND', 'IN2-GND','IN3-GND'),
	'fsr' : ('6.144', '4.096', '2.048', '1.024', '0.512', '0.256'),
	'rate' : ('8', '16', '32'),
}
params={
	'buffer_size': tk.StringVar(value=params_chooses['buffer_size'][0]),
	'non_stop': tk.BooleanVar(value=False),
	'channel': tk.StringVar(value=params_chooses['channel'][0]),
	'fsr': tk.StringVar(value=params_chooses['fsr'][0]),
	'rate': tk.StringVar(value=params_chooses['rate'][0]),
}

# main window
validate_float=win.register(lambda v: (v:=v.replace('.','',1))=='' or (v:=v[1:] if v[0]=='-' else v)=='' or v.isdigit())
validate_int=win.register(lambda v:  v=='' or (v:=v[1:] if v[0]=='-' else v)=='' or v.isdigit())
win.geometry('+%d+100'%((win.winfo_screenwidth()-900)/2))
win.title('Sampler')
control_bar=tk.Frame(win)
control_bar.pack(side='left', anchor='n', fill='y', expand=True)
tk.Label(control_bar, text='ADC chip on the board :', anchor='w').pack(side='top')
ctrl_chip=tk.Label(control_bar, text='ADS1115', anchor='w', relief='sunken')
ctrl_chip.pack(side='top', fill='x', pady=(0, 5))
tk.Label(control_bar, text='Buffer size :').pack(side='top', anchor='w')
ttk.Combobox(control_bar, justify='left', textvariable=params['buffer_size'], values=params_chooses['buffer_size'], state='readonly').pack(side='top', fill='x', pady=(0, 5))
tk.Checkbutton(control_bar, text='Non stop', variable=params['non_stop'], onvalue=True, offvalue=False, anchor='w').pack(side='top', fill='x', pady=(0, 5))
tk.Label(control_bar, text='Sample channel :').pack(side='top', anchor='w')
ttk.Combobox(control_bar, justify='left', textvariable=params['channel'], values=params_chooses['channel'], state='readonly').pack(side='top', fill='x', pady=(0, 5))
tk.Label(control_bar, text='FSR (±V):').pack(side='top', anchor='w')
ttk.Combobox(control_bar, justify='left', textvariable=params['fsr'], values=params_chooses['fsr'], state='readonly').pack(side='top', fill='x', pady=(0, 5))
tk.Label(control_bar, text='Sample rate (SPS) :').pack(side='top', anchor='w')
ctrl_rate=ttk.Combobox(control_bar, justify='left', textvariable=params['rate'], values=params_chooses['rate'], state='readonly')
ctrl_rate.pack(side='top', fill='x', pady=(0, 5))
btn_run=tk.Button(control_bar, text="RUN", command=lambda : run())
btn_run.pack(side='top', anchor='s', fill='x', pady=(5, 5))
btn_trim=tk.Button(control_bar, text="Trim/Invert", state="disabled", command=lambda : trim())
btn_trim.pack(side='top', anchor='s', fill='x', pady=(0, 5))
btn_fft=tk.Button(control_bar, text="FFT", state="disabled", command=lambda : plot_fft())
btn_fft.pack(side='top', anchor='s', fill='x', pady=(0, 5))
btn_filter=tk.Button(control_bar, text="Filter (IIR)", state="disabled", command=lambda : filter_iir())
btn_filter.pack(side='top', anchor='s', fill='x', pady=(0, 5))
tkvar_msg=tk.StringVar(value='')
tk.Label(control_bar, textvariable=tkvar_msg, anchor='nw', justify='left', wraplength=150).pack(side='top', anchor='nw', fill='y', expand=True)
win.protocol("WM_DELETE_WINDOW", lambda: pyplot.close('all') is win.destroy())

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
	dlg.geometry('+'+str(int(wingeo[1])+100)+'+'+str(int(wingeo[2])+200))
	dlg.grab_set()
	dlg.title('Help')
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
				file.write('index,voltage\n')
				idx=0
				print
				for t in data:
					file.write(str(idx)+','+str(t)+'\n')
					idx=idx+1
	except PermissionError as e:
		tk.messagebox.showerror(title='Error', message=e)

#when 'run' button clicked
def run():
	global thread
	if thread is None:
		data_plot.clear()
		axe1.cla()
		axe1.set_ylabel("Voltage (V)")
		axe1.set_xlabel("Sample Points")
		axe1.grid(True)
		canvas.draw()
		try:
			bufsize=int(params['buffer_size'].get())
			data_params['Buffer size']=bufsize
			global data
			data=deque([], maxlen=bufsize)
			count=0 if params['non_stop'].get() else bufsize
			channel=params['channel'].get()
			data_params['Channel']=channel
			channel=params_chooses['channel'].index(channel)
			fsr=params['fsr'].get()
			data_params['Full Scale Range']=fsr
			fsr=params_chooses['fsr'].index(fsr)
			rate=params['rate'].get()
			data_params['Data Rate']=rate
			rate=params_chooses['rate'].index(rate)
			data_params['date time']=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")			
			#start another thread to process
			gc.collect()
			thread=Thread(target=aloop.run_until_complete, args=(proc(count, channel, fsr, rate),))
			thread.start()
			btn_run['text']='STOP'
			btn_run['command']=stop
			btn_save['state']='disabled'
			btn_trim['state']='disabled'
			btn_fft['state']='disabled'
			btn_filter['state']='disabled'
		except Exception as e:
			thread=None
			tk.messagebox.showerror(title='Error', message=e)
			traceback.print_exc()
			return
	win.after(250, wait)

# wait for complete and render the canvas
_last_draw=None
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
		elif msg is q_new_data:
			have_new_data=True
		elif isinstance(msg, QMessage):
			tkvar_msg.set(' '.join(str(s) for s in msg))
	if have_new_data:
		global _last_draw
		if _last_draw is not None:
			_last_draw.remove()
		_last_draw, =axe1.plot(data_plot, color='orange', linewidth=1)
		canvas.draw_idle()
	if thread is not None:
		win.after(250, wait)
	else:
		done()

def plot():
	axe1.cla()
	axe1.set_ylabel("Voltage (V)")
	axe1.set_xlabel("Sample Points : Total "+str(len(data)))
	axe1.grid(True)
	global _last_draw
	if _last_draw is not None:
		_last_draw.remove()
	_last_draw, =axe1.plot(data, color='orange', linewidth=1)
	canvas.draw_idle()
	navbar.update()
	navbar.push_current() 

# complete
def done():
	global data
	data=numpy.array(data, dtype=numpy.float64)
	btn_run['text']='RUN'
	btn_run['command']=run
	btn_save['state']='normal' if len(data)>0 else 'disabled'
	btn_trim['state']='normal' if len(data)>0 else 'disabled'
	btn_fft['state']='normal' if len(data)>0 else 'disabled'
	btn_filter['state']='normal' if len(data)>0 else 'disabled'
	plot()
	
# abort and stop
def stop():
	async def _stop():
		async with await arpc.connect(conf.server['host'], conf.server['port'], password=conf.server['password']) as remote:
			await remote.abort()
	asyncio.run(_stop())

#when trim button is clicked
def trim():
	def _trim():
		global data
		try:
			h=ctrl_head.get().strip()
			t=ctrl_tail.get().strip()
			h=0 if h=='' else max(0, min(len(data)-1, int(h)))
			t=len(data) if t=='' else min(max(int(t)+1, h+1), len(data))
			invt=var_invt.get()
		except ValueError as e:
			tk.messagebox.showerror(title='Error', message=e, parent=dlg)
		else:
			if h>0 or t<len(data):
				data=data[h:t]
			if invt:
				data=data*-1.0
			plot()
			dlg.destroy()
	dlg=tk.Toplevel(win)
	wingeo=win.geometry().split('+')
	dlg.geometry('+'+str(int(wingeo[1])+250)+'+'+str(int(wingeo[2])+200))
	dlg.grab_set()
	dlg.title('Trim')
	tbl=tk.Frame(dlg)
	tbl.pack(side='top', fill='x', padx=10, pady=10, ipadx=10)
	tk.Label(tbl, anchor='e', text='   Keep sample data from index').grid(row=0, column=0, sticky='we')
	ctrl_head=tk.Entry(tbl, width=5, validate='key', validatecommand=(validate_int,'%P'))
	ctrl_head.grid(row=0, column=1, sticky='we')
	tk.Label(tbl, anchor='e', text='   Keep sample data to index').grid(row=1, column=0, sticky='we')
	ctrl_tail=tk.Entry(tbl, width=5, validate='key', validatecommand=(validate_int,'%P'))
	ctrl_tail.grid(row=1, column=1, sticky='we')
	tk.Label(tbl, anchor='e', text='   Invert').grid(row=2, column=0, sticky='we')
	var_invt=tk.BooleanVar(value=False)
	tk.Checkbutton(tbl, variable=var_invt, onvalue=True, offvalue=False).grid(row=2, column=1, sticky='w')
	tk.Button(dlg, text="  OK  ", command=_trim).pack(side='right', padx=5, anchor='n', pady=5)
	tk.Button(dlg, text="CANCEL", command=dlg.destroy).pack(side='right', anchor='n', pady=5)

#plot fft
def plot_fft():
	pyplot.close('all')
	sr=float(data_params['Data Rate'])	
	yf = fft.rfft(data)
	xf = fft.rfftfreq(len(data), 1/sr)
	fig, ax1=pyplot.subplots()
	fig.subplots_adjust(right=0.85)
	ax1.set_title('FFT')
	ax1.set_xlabel('Freq. (Hz)')
	ax1.set_ylabel('Amplitude (V)')
	ax1.grid()
	ax1.plot(xf, numpy.abs(yf), linewidth=1, color='orange')
	pyplot.show()

#when 'Filter (IIR)' button is clicked
def filter_iir():
	sr=float(data_params['Data Rate'])
	def dofilter():
		global data
		try:
			b=tuple(float(t.strip()) for t in var_coef_b.get().split(','))
			a=tuple(float(t.strip()) for t in var_coef_a.get().split(','))
			data=signal.filtfilt(b, a, data)
			plot()
			dlg.destroy()
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e, parent=dlg)
	def show_freq():
		try:
			b=tuple(float(t.strip()) for t in var_coef_b.get().split(','))
			a=tuple(float(t.strip()) for t in var_coef_a.get().split(','))
			w,h=signal.freqz(b, a, worN=250, fs=sr)
			pyplot.close('all')
			fig, ax1=pyplot.subplots()
			fig.subplots_adjust(right=0.85)
			ax1.set_title('Filter frequency response')
			ax1.set_xlabel('Freq. (Hz)')
			ax1.set_ylabel('Response', color='blue')
			ax1.grid()
			ax1.plot(w, numpy.abs(h), linewidth=1, color='blue')
			ax2=ax1.twinx()
			ax2.set_ylabel('Phase (degree)', color='orange')
			ax2.plot(w, numpy.unwrap(numpy.angle(h))*180/3.141592653589793, linewidth=1, color='orange')
			pyplot.show()
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e, parent=dlg)
	def choose_bessel():
		try:
			type=('lowpass','highpass','bandpass','bandstop')[ctrl_bessel_type.current()]
			order=int(ctrl_bessel_order.get().strip())
			freqs=tuple(float(f.strip()) for f in ctrl_bessel_freq.get().split(','))
			for f in freqs:
				assert f<sr/2 and f>0.0, f'frequency must be in (0,{sr/2})'
			b,a=signal.bessel(order, freqs, type, fs=sr)
			var_coef_b.set(','.join(str(x) for x in b))
			var_coef_a.set(','.join(str(x) for x in a))
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e, parent=dlg)
	def choose_butt():
		try:
			type=('lowpass','highpass','bandpass','bandstop')[ctrl_butt_type.current()]
			order=int(ctrl_butt_order.get().strip())
			freqs=tuple(float(f.strip()) for f in ctrl_butt_freq.get().split(','))
			for f in freqs:
				assert f<sr/2 and f>0.0, f'frequency must be in (0,{sr/2})'
			b,a=signal.butter(order, freqs, type, fs=sr)
			var_coef_b.set(','.join(str(x) for x in b))
			var_coef_a.set(','.join(str(x) for x in a))
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e, parent=dlg)
	def choose_cheby1():
		try:
			type=('lowpass','highpass','bandpass','bandstop')[ctrl_cheby1_type.current()]
			order=int(ctrl_cheby1_order.get().strip())
			ripple=int(ctrl_cheby1_ripple.get().strip())
			freqs=tuple(float(f.strip()) for f in ctrl_cheby1_freq.get().split(','))
			for f in freqs:
				assert f<sr/2 and f>0.0, f'frequency must be in (0,{sr/2})'
			b,a=signal.cheby1(order, ripple, freqs, type, fs=sr)
			var_coef_b.set(','.join(str(x) for x in b))
			var_coef_a.set(','.join(str(x) for x in a))
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e, parent=dlg)
	def choose_cheby2():
		try:
			type=('lowpass','highpass','bandpass','bandstop')[ctrl_cheby2_type.current()]
			order=int(ctrl_cheby2_order.get().strip())
			attenuation=float(ctrl_cheby2_attenuation.get().strip())
			freqs=tuple(float(f.strip()) for f in ctrl_cheby2_freq.get().split(','))
			for f in freqs:
				assert f<sr/2 and f>0.0, f'frequency must be in (0,{sr/2})'
			b,a=signal.cheby2(order, attenuation, freqs, type, fs=sr)
			var_coef_b.set(','.join(str(x) for x in b))
			var_coef_a.set(','.join(str(x) for x in a))
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e, parent=dlg)			
	def choose_npc():
		try:
			type=ctrl_npc_type.current()
			assert type>=0 and type<=3
			q=float(ctrl_npc_q.get().strip())
			f=float(ctrl_npc_freq.get().strip())
			assert f<sr/2 and f>0.0, f'frequency must be in (0,{sr/2})'
			if type==0:
				b,a=signal.iirnotch(f, q, fs=sr)
			elif type==1:
				b,a=signal.iirpeak(f, q, fs=sr)
			elif type==2:
				b,a=signal.iircomb(f, q, ftype='notch', fs=sr)
			elif type==3:
				b,a=signal.iircomb(f, q, ftype='peak', fs=sr)
			var_coef_b.set(','.join(str(x) for x in b))
			var_coef_a.set(','.join(str(x) for x in a))
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e, parent=dlg)			
	dlg=tk.Toplevel(win)
	wingeo=win.geometry().split('+')
	dlg.geometry('+'+str(int(wingeo[1])+50)+'+'+str(int(wingeo[2])+50))
	dlg.grab_set()
	dlg.title('Filter')
	tk.Label(dlg, justify='left', text='''\
Choose filter needed with right parameters for it, to generation the appropriate coefficients,
then click 'OK' button. the coefficients are ',' separated, and the Z-domain transfer function is :
	Y(z)/X(z)=(b[0]+b[1]/z+b[2]/z^2+...)/(a[0]+a[1]/z+a[2]/z^2...)
''').pack(side='top', anchor='w')
	tbl_gen=tk.Frame(dlg)
	tbl_gen.pack(side='top', fill='x', ipadx=5, ipady=5)
	irow=0
	tk.Label(tbl_gen, text='Bessel/Thomson : for Band-Pass/Band-Stop, intput 2 Freq. separated by ","').grid(columnspan=9, row=irow, column=0, sticky='w')
	tk.Label(tbl_gen, anchor='w', text='Type :').grid(row=(irow:=irow+1), column=0, sticky='we')
	ctrl_bessel_type=ttk.Combobox(tbl_gen, justify='left', values=('Low-Pass','High-Pass','Band-Pass','Band-Stop'), state='readonly')
	ctrl_bessel_type.current(0)
	ctrl_bessel_type.grid(row=irow, column=1, sticky='we')
	tk.Label(tbl_gen, anchor='e', text='  Order : ').grid(row=irow, column=2, sticky='we')
	ctrl_bessel_order=tk.Entry(tbl_gen, width=5, validate='key', validatecommand=(validate_int,'%P'))
	ctrl_bessel_order.grid(row=irow, column=3, sticky='w')
	tk.Label(tbl_gen, anchor='e', text='  Freq. : ').grid(row=irow, column=4, sticky='we')
	ctrl_bessel_freq=tk.Entry(tbl_gen, width=10)
	ctrl_bessel_freq.grid(row=irow, column=5, sticky='w')
	tk.Button(tbl_gen, text="CHOOSE", command=choose_bessel).grid(row=irow, column=8, padx=5, sticky='e')
	tk.Label(tbl_gen, text='Butterworth : for Band-Pass/Band-Stop, intput 2 Freq. separated by ","').grid(columnspan=9, row=(irow:=irow+1), column=0, sticky='w')
	tk.Label(tbl_gen, anchor='w', text='Type :').grid(row=(irow:=irow+1), column=0, sticky='we')
	ctrl_butt_type=ttk.Combobox(tbl_gen, justify='left', values=('Low-Pass','High-Pass','Band-Pass','Band-Stop'), state='readonly')
	ctrl_butt_type.current(0)
	ctrl_butt_type.grid(row=irow, column=1, sticky='we')
	tk.Label(tbl_gen, anchor='e', text='  Order : ').grid(row=irow, column=2, sticky='we')
	ctrl_butt_order=tk.Entry(tbl_gen, width=5, validate='key', validatecommand=(validate_int,'%P'))
	ctrl_butt_order.grid(row=irow, column=3, sticky='w')
	tk.Label(tbl_gen, anchor='e', text='  Freq. : ').grid(row=irow, column=4, sticky='we')
	ctrl_butt_freq=tk.Entry(tbl_gen, width=10)
	ctrl_butt_freq.grid(row=irow, column=5, sticky='w')
	tk.Button(tbl_gen, text="CHOOSE", command=choose_butt).grid(row=irow, column=8, padx=5, sticky='e')
	tk.Label(tbl_gen, text='Chebyshev I : for Band-Pass/Band-Stop, intput 2 Freq. separated by ","').grid(columnspan=9, row=(irow:=irow+1), column=0, sticky='w')
	tk.Label(tbl_gen, anchor='w', text='Type :').grid(row=(irow:=irow+1), column=0, sticky='we')
	ctrl_cheby1_type=ttk.Combobox(tbl_gen, justify='left', values=('Low-Pass','High-Pass','Band-Pass','Band-Stop'), state='readonly')
	ctrl_cheby1_type.current(0)
	ctrl_cheby1_type.grid(row=irow, column=1, sticky='we')
	tk.Label(tbl_gen, anchor='e', text='  Order : ').grid(row=irow, column=2, sticky='we')
	ctrl_cheby1_order=tk.Entry(tbl_gen, width=5, validate='key', validatecommand=(validate_int,'%P'))
	ctrl_cheby1_order.grid(row=irow, column=3, sticky='w')
	tk.Label(tbl_gen, anchor='e', text='  Freq. : ').grid(row=irow, column=4, sticky='we')
	ctrl_cheby1_freq=tk.Entry(tbl_gen, width=10)
	ctrl_cheby1_freq.grid(row=irow, column=5, sticky='w')
	tk.Label(tbl_gen, anchor='e', text=' Number of Ripples : ').grid(row=irow, column=6, sticky='we')
	ctrl_cheby1_ripple=tk.Entry(tbl_gen, width=5, validate='key', validatecommand=(validate_int,'%P'))
	ctrl_cheby1_ripple.grid(row=irow, column=7, sticky='w')
	tk.Button(tbl_gen, text="CHOOSE", command=choose_cheby1).grid(row=irow, column=8, padx=5, sticky='e')	
	tk.Label(tbl_gen, text='Chebyshev II : for Band-Pass/Band-Stop, intput 2 Freq. separated by ","').grid(columnspan=9, row=(irow:=irow+1), column=0, sticky='w')
	tk.Label(tbl_gen, anchor='w', text='Type :').grid(row=(irow:=irow+1), column=0, sticky='we')
	ctrl_cheby2_type=ttk.Combobox(tbl_gen, justify='left', values=('Low-Pass','High-Pass','Band-Pass','Band-Stop'), state='readonly')
	ctrl_cheby2_type.current(0)
	ctrl_cheby2_type.grid(row=irow, column=1, sticky='we')
	tk.Label(tbl_gen, anchor='e', text='  Order : ').grid(row=irow, column=2, sticky='we')
	ctrl_cheby2_order=tk.Entry(tbl_gen, width=5, validate='key', validatecommand=(validate_int,'%P'))
	ctrl_cheby2_order.grid(row=irow, column=3, sticky='w')
	tk.Label(tbl_gen, anchor='e', text='  Freq. : ').grid(row=irow, column=4, sticky='we')
	ctrl_cheby2_freq=tk.Entry(tbl_gen, width=10)
	ctrl_cheby2_freq.grid(row=irow, column=5, sticky='w')
	tk.Label(tbl_gen, anchor='e', text='  Attenuation (dB) : ').grid(row=irow, column=6, sticky='we')
	ctrl_cheby2_attenuation=tk.Entry(tbl_gen, width=5, validate='key', validatecommand=(validate_float,'%P'))
	ctrl_cheby2_attenuation.grid(row=irow, column=7, sticky='w')
	tk.Button(tbl_gen, text="CHOOSE", command=choose_cheby2).grid(row=irow, column=8, padx=5, sticky='e')	
	tk.Label(tbl_gen, text='Notch/Peak/Comb : Q=Freq/Bandwidth(-3dB)').grid(columnspan=9, row=(irow:=irow+1), column=0, sticky='w')
	tk.Label(tbl_gen, anchor='w', text='Type :').grid(row=(irow:=irow+1), column=0, sticky='we')
	ctrl_npc_type=ttk.Combobox(tbl_gen, justify='left', values=('Notch','Peak','Comb-Notch','Comb-Peak'), state='readonly')
	ctrl_npc_type.current(0)
	ctrl_npc_type.grid(row=irow, column=1, sticky='we')
	tk.Label(tbl_gen, anchor='e', text='  Q value : ').grid(row=irow, column=2, sticky='we')
	ctrl_npc_q=tk.Entry(tbl_gen, width=5, validate='key', validatecommand=(validate_float,'%P'))
	ctrl_npc_q.grid(row=irow, column=3, sticky='w')
	tk.Label(tbl_gen, anchor='e', text='  Freq. : ').grid(row=irow, column=4, sticky='we')
	ctrl_npc_freq=tk.Entry(tbl_gen, width=10,  validate='key', validatecommand=(validate_float,'%P'))
	ctrl_npc_freq.grid(row=irow, column=5, sticky='w')
	tk.Button(tbl_gen, text="CHOOSE", command=choose_npc).grid(row=irow, column=8, padx=5, sticky='e')	
	tbl_coef=ttk.Frame(dlg, padding=(0,10,0,0))
	tbl_coef.pack(side='top', fill='x', ipadx=5, ipady=5)
	var_coef_b=tk.StringVar(value='')
	var_coef_a=tk.StringVar(value='')
	tk.Label(tbl_coef, anchor='w', text='Coef. B :').grid(row=0, column=0, sticky='we')
	tk.Entry(tbl_coef, width=100, textvariable=var_coef_b).grid(row=0, column=1, sticky='we')
	tk.Label(tbl_coef, anchor='w', text='Coef. A :').grid(row=1, column=0, sticky='we')
	tk.Entry(tbl_coef, width=100, textvariable=var_coef_a).grid(row=1, column=1, sticky='we')
	tk.Button(dlg, text="  OK  ", command=dofilter).pack(side='right', padx=5, anchor='n', pady=5)
	tk.Button(dlg, text="  Show Freq. Response  ", command=show_freq).pack(side='right', padx=(5,0), anchor='n', pady=5)
	tk.Button(dlg, text="CANCEL", command=lambda: pyplot.close('all') is dlg.destroy()).pack(side='right', anchor='n', pady=5)
	dlg.protocol("WM_DELETE_WINDOW", lambda: pyplot.close('all') is dlg.destroy())

# process at local end
async def proc(count, channel, fsr, rate):
	global task
	task=asyncio.current_task()
	
	#prepare process variables
	scale_v=float(params_chooses['fsr'][fsr])/32768*calibration['adc'][fsr][1] 
	offset_v=calibration['adc'][fsr][0]
	swapbytes=(sys.byteorder=='little')  #local endian is different
	
	#local RPC
	rpc=arpc.RPC()
	@rpc()
	def message(*args):
		queue.put(QMessage(args))
	@rpc()
	def rawdata(v):  #raw data is base64 encoded, array of 2 bytes unsigned int in BIG endian
		av=array('h')
		av.frombytes(a2b_base64(v))
		if swapbytes: 
			av.byteswap()
		d=tuple(x*scale_v+offset_v for x in av)
		data.extend(d)
		data_plot.extend(d)
		queue.put(q_new_data)

	#main process
	try:
		async with await arpc.connect(conf.server['host'], conf.server['port'], rpc=rpc, password=conf.server['password']) as remote:
			await remote.io2(0) # lighten LEDq_new_data
			await remote.proc(count, channel, fsr, rate)
			await remote.io2(1) # turn off LED
	except BaseException as e:
		queue.put(e)
	task=None

# upload remote program
async def upload():
	async with await arpc.connect(conf.server['host'], conf.server['port'], password=conf.server['password']) as remote:
		global calibration
		calibration=await remote.calibration()
		hw_id=await remote.hw_id()
		if hw_id.split('.')[2].upper()=='SGM58031':
			ctrl_chip['text']='SGM58031'
			params_chooses['rate']=('6.25', '12.5', '25')
			params['rate'].set(params_chooses['rate'][0])
			ctrl_rate['values']=params_chooses['rate']
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
ring_w=0
view_v=memoryview(ring_v)
count=0
done=False
''')
		await remote.exec('''
def on_rdy(pin):
	from init import i2c, ADC_ADDR, rdy
	import __main__	
	from __main__ import buf16, ring_len, ring_v, ring_w, count
	i2c.readfrom_mem_into(ADC_ADDR, 0, buf16)
	ring_v[ring_w]=buf16[0]
	ring_v[ring_w+1]=buf16[1]
	__main__.ring_w=(ring_w+2)%ring_len
	if count>0:
		if (count:=count-1)==0:
			__main__.done=True
			rdy.irq(handler=None, trigger=0)
		else:
			__main__.count=count
''')
		await remote.exec('''
@rpc('proc')
async def proc(count, channel, fsr, rate):
	import gc
	import uasyncio as asyncio
	import arpc
	import __main__
	from __main__ import ring_len, view_v, on_rdy
	from machine import disable_irq, enable_irq, Pin
	from binascii import b2a_base64	
	from init import i2c, ADC_ADDR, rdy

	i2c.writeto_mem(ADC_ADDR, 2, b'\\x00\\x00')
	i2c.writeto_mem(ADC_ADDR, 3, b'\\xff\\xff')
	i2c.writeto_mem(ADC_ADDR, 1, int.to_bytes(((channel&0x07)<<12)|((fsr&0x07)<<9)|((rate&0x07)<<5)|0x8000, 2, 'big'))
	ring_r=0
	__main__.ring_w=0
	__main__.count=count
	__main__.done=False
	remote=arpc.session()
	gc.collect()
	rdy.irq(handler=on_rdy, trigger=Pin.IRQ_FALLING)
	try:
		while not __main__.done or ring_r!=__main__.ring_w:
			await asyncio.sleep_ms(100)
			irqstate=disable_irq()
			ring_last=__main__.ring_w
			enable_irq(irqstate)
			if ring_r>ring_last:
				while ring_r<ring_len:
					end=min(ring_r+256, ring_len)
					await remote.rawdata(b2a_base64(view_v[ring_r:end]), __arpc_toss=True)
					ring_r=end
				ring_r=0
			while ring_r<ring_last:
				end=min(ring_r+256, ring_last)
				await remote.rawdata(b2a_base64(view_v[ring_r:end]), __arpc_toss=True)
				ring_r=end
	finally:
		rdy.irq(handler=None, trigger=0)
		gc.collect()
''')
		await remote.exec('''
import gc
@rpc('abort')
def abort():
	import __main__
	from init import rdy
	__main__.done=True
	rdy.irq(handler=None, trigger=0)
	
gc.collect()
''')


if __name__=='__main__':
	print('Uploading remote program ...')
	asyncio.run(upload())
	print('Done')
	win.mainloop()
	if task is not None:
		aloop.call_soon_threadsafe(task.cancel)

