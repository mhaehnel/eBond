#!/usr/bin/python
'''
  Simulator for network bonded energy aware R.A.I.N
  Copyright (C) 2013 Marcus Haehnel

  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  This program is distributed in the hope that it will be useful, 
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see  <http://www.gnu.org/licenses/>.
'''
import math, argparse, imp, csv, sys

parser = argparse.ArgumentParser(description='Simulate network load and evaluate energy consumption based on card specs')
parser.add_argument('-c','--config',help='The eBond configuration file',required=True)
parser.add_argument('-b','--bwfile',help='The network stats file is in csv format, with timestamp/bandwidth',required=True)
parser.add_argument('-o','--outfile',help='The file that the profile should be written to. CSV Format: timestamp, bandwidth_in, bandwidth_out,power',required=False)
args = parser.parse_args()


cfg = imp.load_source('cfg',args.config)
ifaces = list()
cur_iface = None
cur_iface_time = 0


def selectIface(bw_up,bw_down):
	global ifaces,cur_iface,cur_iface_time
	#Use predictor, go up fast
	bw_up *= 1+float(cfg.PREDICTOR)/100
	bw_down *= 1+float(cfg.PREDICTOR)/100
	#Keep because of hysteresis?
	bw_needed = max(bw_up,bw_down)
	if cur_iface != None:
		if bw_needed < cur_iface.bwrange[1] and (bw_needed > cur_iface.bwrange[0]*float(cfg.HYSTERESIS)/100 or cur_iface_time < float(cfg.KEEPTIME)):
			if (bw_needed > cur_iface.bwrange[0]):
				cur_iface_time = 0
			return cur_iface
	p_min = -1
	p_min_i = None
	for i in ifaces:
		#Get powers for all interfaces ...
		pwr = i.getPower(bw_up,bw_down)
		if pwr == None:
			continue
		if pwr < p_min or p_min == -1:
			p_min = pwr
			p_min_i = i
	cur_iface = p_min_i
	cur_iface_time = 0
	return cur_iface

class Interface:
	def __init__(self, name, bandwidth, latency, touse, profile, rounded):
		self.ifname = name;
		self.bw = (float(bandwidth));
		self.uplatency = latency;
		self.bwrange = touse;
		self.profile = profile;
		self.rounded = rounded;
		start = 0
		start_2 = 0
		end = 0
  		for k in sorted(self.profile.keys()):
			if (int(k[0]) != start):
				print("ERROR in send profile! Is not contignuous: %s vs. %s" % (str(k), start))
			if k[1] > self.bw or k[1] < k[0]:
				print("ERROR strange send range: %s" % str(k))
			
			for p in sorted(self.profile[k]):
				if p[0] != start_2:
					print("ERROR in recv profile! Is not contignuous: %s" % str(p))
				if p[1] > self.bw or p[1] < p[0]:
					print("ERROR strange recv range: %s" % str(p))
				start_2 = p[1]
			start_2 = 0;
			start = k[1];
	
	def __str__(self):
		return "iface: (%s) @ %s MBit/s\nLatency: %s ms\nUse in range: %s MBit/s - %s MBit/s\nProfile: %s\n" % (self.ifname,self.bw,self.uplatency,self.bwrange[0],self.bwrange[1],len(self.profile))

	def getPower(self,bw_up,bw_down):
		power = -1
		#nested lists
		for p in sorted(self.profile.keys()):
			if (float(p[0]) <= (bw_down) and float(p[1]) >= (bw_down)):
				for q in sorted(self.profile[p]):
					if (float(q[0]) <= (bw_up) and float(q[1]) >= (bw_up)):
						power = float(q[2])
						break
				break

		if power == -1:
			if max(bw_up,bw_down) <= self.bw:
				return self.rounded
			else:
				return None
		else:
			return power


	def getMaxBW(self):
		return float(self.bw)

	def getIFace(self):
		return self.ifname
		

print("Reading Interfaces:\n================================");
for i in cfg.INTERFACES:
	print(i)
	iface = Interface(i,eval('cfg.%s_BW' % (i)),eval('cfg.%s_LATENCY' %(i)),eval('cfg.%s_RANGE' %(i)),eval('cfg.%s_PROFILE' %(i)),eval('cfg.%s_ROUND' % (i)))
	print(iface)
	ifaces.append(iface)
print("===== DONE =====\n");
print("ebond timestep = %s s" % (cfg.INTERVAL))
print("Hysteresis = %s %% of max BW " % (cfg.HYSTERESIS))

print(args.bwfile)

total_time = 0
e_total = 0
e_worst = 0
data_total = [0,0]
time_iface = dict()
violations = 0
violation_time = 0
additional_mbytes_next_send = 0
additional_mbytes_next_recv = 0

for i in ifaces:
	time_iface[i.getIFace()] = 0

iface_worst = ifaces[len(ifaces)-1]

if args.outfile:
	profile = open(args.outfile,'w')

still_iface = False
still_time = 0

with  open(args.bwfile,'rb') as csvfile:
	simreader = csv.reader(csvfile, delimiter=',', quotechar="\"");
	#read a new line (first line)
	last_row = simreader.next()
	next_step = float(cfg.INTERVAL)
	#and select the interface to use at this BW
	iface = selectIface(float(last_row[1]),float(last_row[2])) or ifaces[0]
	line = 1
	while True:
		try:
			row = simreader.next()
		except:
			break
		line += 1
		#number of steps to take before the next possible interface change
		#we always take at least one step
		steps = max(1,math.floor((float(row[0])-float(last_row[0]))/float(cfg.INTERVAL)))
		
		#fast forward data and energy values ... no iface changes
		target_time = float(last_row[0]) + steps*float(cfg.INTERVAL)
		while True:
			time = float(row[0]) - float(last_row[0])
			#Can we squeeze in bytes that were too many?
			if (additional_mbytes_next_send != 0 and float(last_row[1]) < iface.getMaxBW()):
				send_add = min((iface.getMaxBW() - float(last_row[1]))*time,additional_mbytes_next_send)
				additional_mbytes_next_send -= send_add
				last_row[1] = float(last_row[1])+send_add/time
			if (additional_mbytes_next_recv != 0 and float(last_row[2]) < iface.getMaxBW()):
				recv_add = min((iface.getMaxBW() - float(last_row[2]))*time,additional_mbytes_next_recv)
				additional_mbytes_next_recv -= recv_add
				last_row[2] = float(last_row[2])+recv_add/time

			#calculate data sent/received
			#data in 
			data_total[0] += float(last_row[1])*time
			#data out
			data_total[1] += float(last_row[2])*time

			#and the power used for this interface
			cur_p = iface.getPower(float(last_row[1]),float(last_row[2]))
			if (cur_p == None):
				additional_mbytes_next_send += max(0,time*(float(row[1]) - iface.getMaxBW()))
				additional_mbytes_next_recv += max(0,time*(float(row[2]) - iface.getMaxBW()))
				cur_p = iface.getPower(min(iface.getMaxBW(),float(last_row[1])),min(iface.getMaxBW(),float(last_row[2])))
					
			if (additional_mbytes_next_send > 0 or additional_mbytes_next_recv > 0):
				violations += 1
				is_violating = 1
				violation_time += time
			else:
				is_violating = 0
			cur_e = cur_p*time
			cur_iface_time += time

			e_total += cur_e
			if still_iface != iface and still_time > 0:
				e_total += still_iface.getPower(0,0)*time
				cur_p += still_iface.getPower(0,0)
				still_time -= time
			e_worst += iface_worst.getPower(float(last_row[1]),float(last_row[2]))*time
			time_iface[iface.getIFace()] += time
			total_time += time

			if args.outfile:
				profile.write('%s,%s,%s,%s,%s\n' % ( last_row[0], last_row[1], last_row[2], cur_p,is_violating))
			if (steps != 1 or row[0] >=  target_time):
				break
			last_row = row
			try:
				row = simreader.next()
			except: 
				break
			line += 1


		#the time spent in this interface

		#only step if we are on the next interval. If we are 
		#select iface for this step
		#either the best fit or the default
		if float(row[0]) >= next_step:
			old_iface = iface
			iface = selectIface(float(last_row[2]),float(last_row[3])) or ifaces[0]
			if old_iface != iface:
				still_time = float(eval('cfg.%s_LATENCY' % (old_iface.getIFace()))/1000)
				still_iface = old_iface
				time_iface[old_iface.getIFace()] += float(eval('cfg.%s_LATENCY' % (old_iface.getIFace())))/1000
			next_step += float(cfg.INTERVAL)

		last_row = row

		if line%100 == 0:
			if iface.getIFace() == 'eth1':
				sys.stdout.write(".")
			else:
				sys.stdout.write("|")
			sys.stdout.flush()

print("DONE")
print("Simulated Time (days): %s" % (total_time/3600/24))
print("Achived Energy: %s MJ (vs %s MJ for only high power card => %s %% saved!)" % (e_total/1000000,e_worst/1000000,(e_worst-e_total)*100/e_worst))
print("Consumed Power: %s Wh (vs %s Wh)" %( str(e_total/3600),str(e_worst/3600)))
print("Interface up share: ")
for i in time_iface.keys():
	print("%s => %s %%" % (i, time_iface[i]*100/total_time))
print("Number of service vialoations due to late power up: %s (%s seconds or %s %% of time)" %(violations,violation_time,violation_time*100/total_time))
print("Remaining bytes to transfer: %s / %s" %(additional_mbytes_next_send,additional_mbytes_next_recv))
print("Transfered GByte: %s / %s / %s " % (data_total[0]/1024/8,data_total[1]/1024/8,(data_total[1]+data_total[0])/1024/8))
print("Average Speed MByte/s: %s / %s / %s" % (data_total[0]/8/total_time,data_total[1]/8/total_time,(data_total[1]+data_total[0])/8/total_time))

	
