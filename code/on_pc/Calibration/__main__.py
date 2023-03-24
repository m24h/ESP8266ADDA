import os
import sys
import gc
import json
import traceback
import asyncio
import tkinter as tk
import tkinter.messagebox
import tkinter.filedialog

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) 
import arpc
import conf

#application ID
app_id='Calibration.'+str(os.path.getmtime(__file__))
data_file_id='esp8266_4adc_4dac.Calibration.1'
#main window
win = tk.Tk()
#default calibration data
cali_def={
	'vref':2.5,
	'adc':([0,1],[0,1],[0,1],[0,1],[0,1],[0,1]),  # (offset, factor) for range (6.144, 4.096, 2.048, 1.024, 0.512, 0.256, 0.256, 0.256)
	'dac':([0,1],[0,1],[0,1],[0,1]) # (offset, factor) for (ch.0, ch.1, ch.2, ch.3)
}
# control parameters from calibration data
params={
	'vref':tk.DoubleVar(),
	'adc':([tk.DoubleVar(),tk.DoubleVar()], [tk.DoubleVar(),tk.DoubleVar()], [tk.DoubleVar(),tk.DoubleVar()], [tk.DoubleVar(),tk.DoubleVar()], [tk.DoubleVar(),tk.DoubleVar()],  [tk.DoubleVar(),tk.DoubleVar()]),
	'dac':([tk.DoubleVar(),tk.DoubleVar()], [tk.DoubleVar(),tk.DoubleVar()], [tk.DoubleVar(),tk.DoubleVar()], [tk.DoubleVar(),tk.DoubleVar()])
}

# update 'params' from 'cali' data
def params_update(p, c):
	if isinstance(p, dict):
		for k in p.keys():
			params_update(p[k], c[k])
	elif isinstance(p, list) or isinstance(p, tuple):
		for i in range(len(p)):
			params_update(p[i], c[i])
	else:
		p.set(c)

# get from 'params'
def params_get(p):
	if isinstance(p, dict):
		return dict((k, params_get(v)) for k,v in p.items())
	elif isinstance(p, list) or isinstance(p, tuple):
		return tuple(params_get(t) for t in p)
	return p.get()

# set main window
win.geometry('+%d+100'%((win.winfo_screenwidth()-600)/2))
win.title('Calibration Tool')
validate_float=win.register(lambda v: (v:=v.replace('.','',1))=='' or (v:=v[1:] if v[0]=='-' else v)=='' or v.isdigit())

#toolbar
toolbar=tk.Frame(win)
toolbar.pack(side='top', fill='x')
tk.Button(toolbar, text="Restore to default", command=lambda : restore()).pack(side='left')
tk.Button(toolbar, text="Load from file", command=lambda : loadfile()).pack(side='left')
tk.Button(toolbar, text="Save to file", command=lambda : savefile()).pack(side='left')
tk.Button(toolbar, text="Load from board", command=lambda : load()).pack(side='left')
tk.Button(toolbar, text="Save to board", command=lambda : save()).pack(side='left')
tk.Button(toolbar, text="Help", command=lambda : help('''
1. The approximate process is to load data from the board,
    measure and fill in the correct intercept offset and slope factor,
    then save data back to  board.
2. Before calibrate DAC, Vref must be calibrated first.
3. Before calibrate ADC, Wiring OUT_A to IN_0, OUT_B to IN_1, 
    this will facilitate calibration by using DAC as voltage source.
4. A high-precision voltmeter is needed to measure and fill in data, 
    even if the DAC is being used as a voltage source, 
    the actual voltage generated still needs to be measured by it,
    whatever if DAC output has been calibrated or not.
5. To avoid cross-jamming, the voltmeter used should be completely isolated
    from this board on power supply (preferably battery-powered for one of the two)
6. For DAC, <real output>=<DAC data>/4096*Vref*2*factor+offset .
7. For ADC, <real input>=<sample data>/32768*FSR*factor+offset .
8. Due to accuracy of MicroPython, the calibration data may be truncated after saving to board.
''')).pack(side='left')

# vref frame
frame_vref=tk.LabelFrame(win, text='Vref for DAC (Calibrate this before DAC calibration)', labelanchor='nw')
frame_vref.pack(side='top', anchor='nw', fill='x', ipadx=5, ipady=5, padx=5, pady=5)
tk.Label(frame_vref, text='Measured :').pack(side='left')
tk.Entry(frame_vref, width=10, textvariable=params['vref'], validate='key', validatecommand=(validate_float,'%P')).pack(side='left')
tk.Label(frame_vref, text='V').pack(side='left')

#dac frame
frame_dac=tk.LabelFrame(win, text="DAC (Each channel needs to be calibrated individually)", labelanchor="nw")
frame_dac.pack(side='top', anchor='nw', fill='x', ipadx=5, ipady=5, padx=5, pady=5)
for i,n in ((0,'OUT_A'),(1,'OUT_B'),(2,'OUT_C'),(3,'OUT_D')):
	tk.Label(frame_dac, text=n).grid(row=i, column=0, sticky='w')
	tk.Label(frame_dac, text=' Offset :').grid(row=i, column=1, sticky='w')
	tk.Entry(frame_dac, width=10, textvariable=params['dac'][i][0], validate='key', validatecommand=(validate_float,'%P')).grid(row=i, column=2, sticky='w')
	tk.Label(frame_dac, text='  Factor :').grid(row=i, column=3, sticky='w')
	tk.Entry(frame_dac, width=10, textvariable=params['dac'][i][1], validate='key', validatecommand=(validate_float,'%P')).grid(row=i, column=4, sticky='w')
	tk.Button(frame_dac, text="Measure", command=lambda ch=i, name=n: measure_dac(ch, name)).grid(row=i, column=5, sticky='w', padx=(5,5))
	
#adc frame
frame_adc=tk.LabelFrame(win, text="ADC (Channel consistency is good, each range needs to be calibrated individually)", labelanchor="nw")
frame_adc.pack(side='top', anchor='nw', fill='x', ipadx=5, ipady=5, padx=5, pady=5)
for j,r,s in ((0,6.144,'±6.144V'),(1,4.096,'±4.096V'),(2,2.048,'±2.048V'),(3,1.024,'±1.024V'),(4,0.512,'±0.512V'),(5,0.256,'±0.256V')):
	row=j
	tk.Label(frame_adc, text=s).grid(row=row, column=0, sticky='w')
	tk.Label(frame_adc, text=' Offset :').grid(row=row, column=1, sticky='w')
	tk.Entry(frame_adc, width=10, textvariable=params['adc'][j][0], validate='key', validatecommand=(validate_float,'%P')).grid(row=row, column=2, sticky='w')
	tk.Label(frame_adc, text='  Factor :').grid(row=row, column=3, sticky='w')
	tk.Entry(frame_adc, width=10, textvariable=params['adc'][j][1], validate='key', validatecommand=(validate_float,'%P')).grid(row=row, column=4, sticky='w')
	tk.Button(frame_adc, text="Measure", command=lambda rng=j, fsr=r, name=s : measure_adc(rng, fsr, name)).grid(row=row, column=5, sticky='w', padx=(5,5))
	
#update entry text
params_update(params, cali_def)

# 'help' dialog
def help(txt):
	dlg=tk.Toplevel(win)
	wingeo=win.geometry().split('+')
	dlg.geometry('+'+str(int(wingeo[1])+50)+'+'+str(int(wingeo[2])+100))
	dlg.grab_set()
	dlg._txt=txt
	dlg.title('Help')
	tk.Label(dlg, justify='left', bg='white', text=dlg._txt).pack(side='top', anchor='nw', ipadx=5, fill='x')

#restore calibration data to default	
def restore():
	if tk.messagebox.askokcancel ('Promt', 'Are you sure?\nThe current data will be discarded.'): 
		params_update(params, cali_def)
		tk.messagebox.showinfo('Done', 'All data have been restored to default.\nTrust the Native !' )

#load data from file
def loadfile():
	try:
		if (file:=tk.filedialog.askopenfile(title='Open Config File', defaultextension='.json', filetypes=[('JSON file','*.json'),('All files','*')])):
			with file:
				t=json.load(file)
				if '_for' in t and t['_for']==data_file_id:
					params_update(params, t)
					tk.messagebox.showinfo('Done', 'File is loaded.\nOh, holy-backup !' )
				else:
					tk.messagebox.showerror(title='Error', message='This file is not my style')
	except Exception as e:
		tk.messagebox.showerror(title='Error', message=e)

#save data to file
def savefile():
	try:
		if (file:=tk.filedialog.asksaveasfile(title='Save Config File', defaultextension='.json', filetypes=[('JSON file','*.json'),('All files','*')])):
			with file:
				t=params_get(params)
				json.dump({'_for':data_file_id, **t}, file)
			tk.messagebox.showinfo('Done', 'File is saved.\nYou are so robust !' )
	except Exception as e:
		tk.messagebox.showerror(title='Error', message=e)

#load calibration setting from board
async def _load():
	async with await arpc.connect(conf.server['host'], conf.server['port'], password=conf.server['password']) as remote:
		return await remote.calibration()
def load():
	if tk.messagebox.askokcancel ('Promt', 'Are you sure?\nThe current data will be discarded.'): 	
		try:
			params_update(params, asyncio.run(_load()))
			tk.messagebox.showinfo('Done', "Data have been loaded from the board.\nCheck the soul !")
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e)	

#send and set calibration data to board, 
async def _save(cali):
	async with await arpc.connect(conf.server['host'], conf.server['port'], password=conf.server['password']) as remote:
		await remote.exec(f'''
rpc['calibration']={repr(cali)}
with open('/cali.py', 'w') as _file_cali_py:
	_file_cali_py.write('from __main__ import rpc\\n')
	_file_cali_py.write("rpc['calibration']="+repr(rpc['calibration'])+'\\n')
del _file_cali_py
import gc
gc.collect()
''')
def save():
	if tk.messagebox.askokcancel ('Promt', 'Are you sure?\nData on the board will be discarded.'):	
		try:
			asyncio.run(_save(params_get(params)))
			tk.messagebox.showinfo('Done', "Data have been saved to the board.\nGood luck !")
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e)	

#least squares method
def leastsq(X, Y):
	d=dict((x,y) for x,y in zip(X,Y) if x is not None and y is not None)
	n=len(d)
	assert n>1, 'Alt least 2 different points is needed for calculating factor and offset'
	xy=sum(x*y for x,y in d.items())
	x2=sum(x*x for x in d.keys())
	sx=sum(x for x in d.keys())
	sy=sum(y for y in d.values())
	k=(xy-sx*sy/n)/(x2-sx*sx/n)
	return ((sy-k*sx)/n, k)

#least squares method for factor, offset is sepcified
def leastsq_factor(X, Y, offset=0):
	d=dict((x,y) for x,y in zip(X,Y) if x is not None and y is not None)
	n=len(d)
	assert n>1, 'Alt least 2 different points is needed for calculating factor and offset'
	xy=sum(x*y for x,y in d.items())
	x2=sum(x*x for x in d.keys())
	sx=sum(x for x in d.keys())
	return (xy-offset*sx)/x2

async def _dac_out(ch, v):
	async with await arpc.connect(conf.server['host'], conf.server['port'], password=conf.server['password']) as remote:
		await remote.dac_write(ch, 0, 0, v)
	pass
def dac_out(ch, v, parent):
	try:
		asyncio.run(_dac_out(ch, v))
	except Exception as e:
		tk.messagebox.showerror(title='Error', message=e, parent=parent)

#dlg for measure and calculate DAC calibration data
def measure_dac(ch, name):
	output=list()
	measured=list() # list of (DAC internal code, Entry)
	try:
		vref=params['vref'].get()
	except Exception as e:
		tk.messagebox.showerror(title='Error', message=e)
		return
	def done():
		try:
			m=tuple(t.get().strip() for t in measured)
			m=tuple(None if t=='' else float(t) for t in m)
			b,k=leastsq(output, m)
			params['dac'][ch][0].set(b)
			params['dac'][ch][1].set(k)
			dlg.destroy()
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e, parent=dlg)
	dlg=tk.Toplevel(win, padx=5, pady=5)
	wingeo=win.geometry().split('+')
	dlg.geometry('+'+str(int(wingeo[1])+50)+'+'+str(int(wingeo[2])+50))	
	dlg.grab_set()
	dlg.title('DAC Measurement for '+name)
	tk.Label(dlg, justify='left', text=\
'''Make DAC channel output voltage, then measure and fill in the input box, 
Once there are enough data points, press the 'Finish' button to calculate and return.
There is no need to fill in all the input box (2 is at least needed), 
but the more measurements and inputs, the more accurate the results.'''
).pack(side='top', anchor='w')
	tbl=tk.Frame(dlg)
	tbl.pack(side='top', anchor='w')
	for i in range (11):
		s=round((vref*2-1)*i/10+0.5, 2)
		v=int(s/(vref*2)*4096)
		output.append(v/4096*vref*2)
		tk.Button(tbl, text=f'Output {s:3.2f} V', command=lambda v=v : dac_out(ch, v, dlg)).grid(row=i, column=0,sticky='we')
		tk.Label(tbl, text='  then Measured :').grid(row=i, column=1, sticky='e')
		(t:=tk.Entry(tbl, width=10, text='', validate='key', validatecommand=(validate_float,'%P'))).grid(row=i, column=2, sticky='w')
		measured.append(t)
		tk.Label(tbl, text='V').grid(row=i, column=3, sticky='w')
	tk.Button(dlg, text='Finish', command=done).pack(side='right', anchor='e')
	tk.Button(dlg, text='Cancel', command=dlg.destroy).pack(side='right', anchor='e', padx=3)

async def _dac_diff_out(a, b):
	async with await arpc.connect(conf.server['host'], conf.server['port'], password=conf.server['password']) as remote:
		await remote.dac_write(0, 0, 0, a, False)
		await remote.dac_write(1, 0, 0, b, False)
		await remote.dac_load()
def dac_diff_out(v, parent):
	a=2048+v//2
	b=a-v	
	try:
		asyncio.run(_dac_diff_out(a, b))
	except Exception as e:
		tk.messagebox.showerror(title='Error', message=e, parent=parent)

async def _adc_sample(rng):
	async with await arpc.connect(conf.server['host'], conf.server['port'], password=conf.server['password']) as remote:
		await remote.adc_config(0, rng, 3)
		s=0
		for i in range(5):
			await asyncio.sleep(0.05)
			s=s+await remote.adc_read()
		return s/5
def adc_sample(label, rng, fsr, parent):
	try:
		label['text']=str(asyncio.run(_adc_sample(rng))/32768*fsr)
	except Exception as e:
		tk.messagebox.showerror(title='Error', message=e, parent=parent)

#dlg for measure and calculate ADC calibration data
def measure_adc(rng, fsr, name):
	sampled=list()
	measured=list()
	try:
		vref=params['vref'].get()
	except Exception as e:
		tk.messagebox.showerror(title='Error', message=e)
		return
	def done(no_offset):
		try:
			s=tuple(t['text'].strip() for t in sampled)
			s=tuple(None if t=='' else float(t) for t in s)
			m=tuple(t.get().strip() for t in measured)
			m=tuple(None if t=='' else float(t) for t in m)
			if no_offset:
				b=0
				k=leastsq_factor(s, m)
			else:
				b,k=leastsq(s, m)
			params['adc'][rng][0].set(b)
			params['adc'][rng][1].set(k)
			dlg.destroy()
		except Exception as e:
			tk.messagebox.showerror(title='Error', message=e, parent=dlg)
	dlg=tk.Toplevel(win, padx=5, pady=5)
	wingeo=win.geometry().split('+')
	dlg.geometry('+'+str(int(wingeo[1])+50)+'+'+str(int(wingeo[2])+50))	
	dlg.grab_set()
	dlg.title('ADC Measurement for '+name)
	tk.Label(dlg, justify='left', text=\
'''A stable and variable positive and negative voltage source (±6.144V) is required, 
connect its output to IN_0 and IN_1 for sampling and measurement.
Directly using the the DAC output is more convenient, just connect OUT_A to IN_0, OUT_B to IN_1.
Control the voltage source (or press 'DAC output' button when needed), 
press the 'Sample' button to obtain the ADC sampling results for comparison,
measure the actual voltage using a voltmeter and fill in the input box at the same time.
Once there are enough data points, press the 'Finish' button to calculate and return.
In most cases, 'Finish (no offset)' is recommended because in reality the offset of the ADC should be 0.
'''
).pack(side='top', anchor='w')
	tbl=tk.Frame(dlg)
	tbl.pack(side='top', anchor='w')
	for i in range (11):
		v=round((i*fsr*2/10-fsr)*0.9, 3)
		v=max(-vref*1.8, min(vref*1.8, v))
		tk.Button(tbl, text=f'DAC Output {v:4.3f} V', command=lambda v=int(v*4096/vref/2) : dac_diff_out(v, dlg)).grid(row=i, column=0,sticky='we')
		tk.Button(tbl, text='Sample', command=lambda idx=i : adc_sample(sampled[idx], rng, fsr, dlg)).grid(row=i, column=1, padx=5, sticky='we')
		(t:=tk.Label(tbl, text='', relief='sunken', width=10, anchor='w')).grid(row=i, column=2)
		sampled.append(t)
		tk.Label(tbl, text='V  and Measured :').grid(row=i, column=3, sticky='e')
		(t:=tk.Entry(tbl, width=10, text='', validate='key', validatecommand=(validate_float,'%P'))).grid(row=i, column=4, sticky='w')
		measured.append(t)
		tk.Label(tbl, text='V').grid(row=i, column=5, sticky='w')
	tk.Button(dlg, text='Finish (no offset)', command=lambda: done(True)).pack(side='right', anchor='e')
	tk.Button(dlg, text='Finish', command=lambda: done(False)).pack(side='right', anchor='e', padx=3)
	tk.Button(dlg, text='Cancel', command=dlg.destroy).pack(side='right', anchor='e', padx=3)

if __name__=='__main__':
	win.mainloop()

