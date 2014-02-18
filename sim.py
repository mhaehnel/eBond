#!/usr/bin/env python3
'''
  Simulator for network bonded energy aware R.A.I.N
  Copyright (C) 2013-2014 Marcus Haehnel

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

ifaces = []
cur_iface = None
cur_iface_time = 0

def selectIface(bw_up,bw_down):
    global ifaces,cur_iface,cur_iface_time
    #Use predictor, go up fast
    bw_up *= 1+cfg.PREDICTOR/100.0
    bw_down *= 1+cfg.PREDICTOR/100.0
    #Keep because of hysteresis?
    bw_needed = max(bw_up,bw_down) #TODO: This assumes symmetric up/down
    if cur_iface:
        #Check for keep time and hysteresis
        if bw_needed < max(cur_iface.bwrange) and (bw_needed > min(cur_iface.bwrange)*cfg.HYSTERESIS/100.0 or cur_iface_time < float(cfg.KEEPTIME)):
            #Reset cooldown timer if we need this interface!
            if bw_needed > min(cur_iface.bwrange):
                cur_iface_time = 0
            return cur_iface

    cur_iface = min(ifaces,key=lambda x: x.getPower(bw_up,bw_down) or float("inf"))
    cur_iface_time = 0
    return cur_iface

class Interface:
    def __init__(self, name):
        self.ifname = name;
        self.bw = float(eval('cfg.%s_BW' % (name)))
        self.uplatency = eval('cfg.%s_LATENCY' %(name))
        self.bwrange = eval('cfg.%s_RANGE' %(name))
        self.profile = eval('cfg.%s_PROFILE' %(name))
        self.rounded = eval('cfg.%s_ROUND' % (name))
        cur_send = 0
        #Check if profiles are contiguous and sane
        for k in sorted(self.profile.keys()):
            if int(k[0]) != cur_send:
                print("ERROR in send profile! Is not contiguous: %s vs. %s" % (str(k), cur_send))
            if not k[0] <= k[1] <= self.bw:
                print("ERROR strange send range: %s" % str(k))

            cur_recv = 0;
            for p in sorted(self.profile[k]):
                if p[0] != cur_recv:
                    print("ERROR in recv profile! Is not contiguous: %s vs. %s" % str(p), cur_recv)
                if not p[0] <= p[1] <= self.bw:
                    print("ERROR strange recv range: %s" % str(p))
                cur_recv = p[1]
            cur_send = k[1];

    def __str__(self):
        return ('''iface: {self.ifname} @ {self.bw} MBit/s\n'''
                '''Latency: {self.uplatency} ms\n'''
                '''Use in range: {self.bwrange[0]}  MBit/s - {self.bwrange[1]} MBit/s\n'''
                '''Profile: {length}\n'''.format(self=self,length=len(self.profile)))
    def getPower(self,bw_up,bw_down):
        #nested lists
        for snd in sorted(self.profile.keys()):
            if float(snd[0]) <= bw_down < float(snd[1]):
                for recv in sorted(self.profile[snd]):
                    if float(recv[0]) <= bw_up < float(recv[1]):
                        return float(recv[2])

                break
        if max(bw_up,bw_down) <= self.bw:
            return self.rounded
        return None

    def getMaxBW(self):
        return float(self.bw)

    def getIFace(self):
        return self.ifname


print("Reading Interfaces:\n================================");
ifaces = [ Interface(i) for i in cfg.INTERFACES ]
for i in ifaces:
    print(i)
print("===== DONE =====\n");
print("ebond timestep = %s s" % (cfg.INTERVAL))
print("Hysteresis = %s %% of max BW " % (cfg.HYSTERESIS))

print(args.bwfile)

total_time = 0
e_total = 0
e_worst = 0
data_total = [0,0]
violations = 0
violation_time = 0
additional_mbytes_next_send = 0
additional_mbytes_next_recv = 0

time_iface = { i.getIFace() : 0 for i in ifaces }

iface_worst = ifaces[len(ifaces)-1]

if args.outfile:
    profile = open(args.outfile,'w')

still_iface = False
still_time = 0

with  open(args.bwfile,'rt') as csvfile:
    simreader = csv.reader(csvfile, delimiter=',', quotechar="\"");
    #read a new line (first line)
    last_row = next(simreader)
    next_step = float(cfg.INTERVAL)
    #and select the interface to use at this BW
    iface = selectIface(float(last_row[1]),float(last_row[2])) or ifaces[0]
    line = 1
    while True:
        try:
            row = next(simreader)
        except:
            break
        line += 1
        #number of steps to take before the next possible interface change
        #we always take at least one step, step = bandwidth log interval
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
            if not cur_p:
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

            if line%100 == 0:
                if iface.getIFace() == 'eth1':
                    sys.stdout.write(".")
                else:
                    sys.stdout.write("|")
                sys.stdout.flush()

            if args.outfile:
                profile.write('%s,%s,%s,%s,%s\n' % ( last_row[0], last_row[1], last_row[2], cur_p,is_violating))
            if (steps != 1 or float(row[0]) >=  target_time):
                break
            last_row = row
            try:
                row = next(simreader)
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
                still_time = old_iface.uplatency/1000.0
                still_iface = old_iface
                time_iface[old_iface.getIFace()] += old_iface.uplatency/1000.0
            next_step += float(cfg.INTERVAL)

        last_row = row

print("DONE")
print("Simulated Time (days): %s" % (total_time/3600/24))
print("Achived Energy: %s MJ (vs %s MJ for only high power card => %s %% saved!)" % (e_total/1000000,e_worst/1000000,(e_worst-e_total)*100/e_worst))
print("Consumed Power: %s Wh (vs %s Wh)" %( str(e_total/3600),str(e_worst/3600)))
print("Interface up share: ")
for key,value in time_iface.items():
    print("%s => %s %%" % (key, value*100/total_time))
print("Number of service vialoations due to late power up: %s (%s seconds or %s %% of time)" %(violations,violation_time,violation_time*100/total_time))
print("Remaining bytes to transfer: %s / %s" %(additional_mbytes_next_send,additional_mbytes_next_recv))
print("Transfered GByte: %s / %s / %s " % (data_total[0]/1024/8,data_total[1]/1024/8,(data_total[1]+data_total[0])/1024/8))
print("Average Speed MByte/s: %s / %s / %s" % (data_total[0]/8/total_time,data_total[1]/8/total_time,(data_total[1]+data_total[0])/8/total_time))

