#!/usr/bin/env python3
"""Read-only Linux/XUI diagnostic report. Python 3.8+, standard library only."""
import argparse
from collections import Counter
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

SKIP={'streams','movies','series','vod','content','backups','backup','logs','log','cache','tmp','temp','sessions','credentials','keys','certificates','node_modules','.git','user_data'}
EXT={'.php','.py','.js','.ts','.json','.sh','.conf','.ini','.sql','.so','.service'}
SERVICE_PATTERN=r'xui|xtream|iptv|nginx|php|maria|mysql|redis|keydb|ffmpeg|supervisor'
REPORT_VERSION='xui-structure-readonly-v2'

def command(args, timeout=8):
    if not shutil.which(args[0]):
        return {'available':False}
    try:
        env=None
        if Path(args[0]).name in {'mysql','mariadb'}:
            env={k:v for k,v in os.environ.items() if not k.startswith(('MYSQL','MARIADB'))}
            # MySQL may read .mylogin.cnf even with --no-defaults. Override it.
            env['MYSQL_TEST_LOGIN_FILE']=os.devnull
        p=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',errors='replace',
                         timeout=timeout,check=False,env=env)
        return {'available':True,'returncode':p.returncode,'stdout':p.stdout[:1_000_000],
                'truncated':len(p.stdout)>1_000_000}
    except (OSError,subprocess.TimeoutExpired):
        return {'available':True,'error':'command_failed_or_timed_out'}

def safe_path(path):
    return '/'.join('[redacted-name]' if re.search(r'(?i)(?:password|passwd|secret|credential|token|private.key)',part) or len(part)>100 or re.search(r'[\x00-\x1f\x7f]',part) else part for part in str(path).split('/'))

def inventory(root, max_items=2000, depth=4):
    result=[];root=Path(root)
    if not root.is_dir() or root.is_symlink():
        return {'root':safe_path(root),'available':False}
    limited=False;top=[]
    try:
        for p in sorted(root.iterdir())[:200]:
            st=p.lstat()
            top.append({'name':safe_path(p.name),'kind':'symlink' if stat.S_ISLNK(st.st_mode) else 'directory' if stat.S_ISDIR(st.st_mode) else 'file',
                        'size_bytes':st.st_size,'mode':oct(st.st_mode&0o7777),'uid':st.st_uid,'gid':st.st_gid,
                        'contents_skipped':p.name.lower() in SKIP})
    except OSError:pass
    for current,dirs,files in os.walk(root,followlinks=False):
        here=Path(current);level=len(here.relative_to(root).parts)
        dirs[:]=sorted(d for d in dirs if d.lower() not in SKIP and not (here/d).is_symlink()) if level<depth else []
        for name in sorted(files):
            p=here/name
            if (p.suffix.lower() not in EXT and not level==0) or p.is_symlink():
                continue
            try:
                st=p.stat();result.append({'path':safe_path(p.relative_to(root)),'size_bytes':st.st_size,'mode':oct(st.st_mode&0o777),'uid':st.st_uid})
            except OSError:
                continue
            if len(result)>=max_items:
                limited=True;break
        if limited:break
    return {'root':safe_path(root),'available':True,'top_level':top,'files':result,'truncated':limited,
            'max_files':max_items,'max_depth':depth,'excluded_directory_names':sorted(SKIP)}

def roots():
    candidates=[Path('/home/xui'),Path('/home/xtreamcodes/iptv_xtream_codes'),Path('/opt/xui'),Path('/var/www/html'),Path('/usr/local/nginx'),Path('/etc/nginx'),Path('/etc/cron.d')]
    for parent in (Path('/home'),Path('/opt'),Path('/var/www')):
        try:
            candidates.extend(p for p in parent.iterdir() if p.is_dir() and re.search('xui|xtream|iptv',p.name,re.I))
        except OSError:pass
    seen=set();found=[]
    for p in candidates:
        if str(p) not in seen:
            seen.add(str(p));found.append(inventory(p))
    return found

def os_info():
    data={}
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if '=' in line:
                k,v=line.split('=',1)
                if k in {'ID','VERSION_ID','PRETTY_NAME'}:data[k]=v.strip('"')
    except OSError:pass
    data['kernel']=os.uname().release;data['architecture']=os.uname().machine;data['logical_cpu_count']=os.cpu_count()
    data['python_version']='.'.join(map(str,sys.version_info[:3]))
    data['running_as_root']=os.geteuid()==0
    try:
        data['uptime_seconds']=float(Path('/proc/uptime').read_text().split()[0])
        data['load_average']=list(os.getloadavg())
    except (OSError,ValueError,IndexError):pass
    try:
        cpu=Path('/proc/cpuinfo').read_text()
        model=re.search(r'^(?:model name|Hardware)\s*:\s*(.+)',cpu,re.M)
        if model:data['cpu_model']=model.group(1)[:200]
    except OSError:pass
    try:
        mem=Path('/proc/meminfo').read_text();m=re.search(r'^MemTotal:\s+(\d+)',mem,re.M)
        if m:data['ram_total_bytes']=int(m.group(1))*1024
    except OSError:pass
    return data

def services():
    r=command(['systemctl','list-units','--type=service','--all','--plain','--no-legend','--no-pager'])
    if r.get('returncode')!=0:return {'available':False}
    items=[]
    for line in r['stdout'].splitlines():
        fields=line.lstrip('● ').split()
        if len(fields)>=4 and re.search(SERVICE_PATTERN,fields[0],re.I):
            items.append(dict(zip(['unit','load','active','sub'],[safe_path(x) for x in fields[:4]])))
    return {'available':True,'items':items}

def listener_ports():
    return sorted({row['port'] for row in connections()['tcp_listeners']})

def web_routes():
    routes=[];seen=set();read=0
    for root in (Path('/etc/nginx'),Path('/usr/local/nginx/conf'),Path('/home/xui/bin/nginx/conf')):
        if not root.is_dir() or root.is_symlink():continue
        for file in root.rglob('*.conf'):
            if read>=100:break
            if file.is_symlink() or str(file) in seen:continue
            seen.add(str(file))
            try:
                if file.stat().st_size>2_000_000:continue
                content=file.read_text(errors='replace');read+=1
            except OSError:continue
            for line in content.splitlines():
                text=line.strip()
                if text.startswith('location '):
                    value=text.split('{',1)[0].strip()
                    value=re.sub(r'[A-Za-z0-9_-]{20,}','[redacted-segment]',value)
                    if re.search(r'(?i)password|passwd|token|secret|credential',value):value='location [redacted]'
                    routes.append(value[:240])
    return {'available':read>0,'configuration_files_scanned':read,'location_directives':sorted(set(routes))[:300]}

def database_schema(requested=None):
    binary=shutil.which('mysql') or shutil.which('mariadb')
    if not binary:return {'available':False,'reason':'No database client found'}
    base=[binary,'--no-defaults','--protocol=socket','--password=','--connect-timeout=3','--batch','--skip-column-names']
    dbs=command(base+['-e','SHOW DATABASES'])
    if dbs.get('returncode')!=0:return {'available':False,'reason':'Local socket authentication unavailable; no credentials read or requested'}
    names=[n.strip() for n in dbs['stdout'].splitlines() if re.fullmatch(r'[A-Za-z0-9_$-]{1,64}',n.strip()) and
           (n.strip() in requested if requested else re.search(r'xui|xtream|iptv',n,re.I))][:10]
    result=[]
    for name in names:
        quoted='0x'+name.encode('utf-8').hex()
        sql='SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='+quoted+' ORDER BY TABLE_NAME,ORDINAL_POSITION LIMIT 5000'
        r=command(base+['-e',sql])
        columns=[]
        if r.get('returncode')==0:
            for line in r['stdout'].splitlines():
                fields=line.split('\t')
                if len(fields)==5:columns.append(dict(zip(['table','column','type','nullable','key'],fields)))
        item={'schema':name,'columns':columns,'available':r.get('returncode')==0,
              'columns_limit_reached':len(columns)>=5000 or r.get('truncated',False)}
        for key,fields,sql in (
            ('tables',['table','engine','estimated_rows','data_bytes','index_bytes'],
             'SELECT TABLE_NAME,ENGINE,TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH FROM information_schema.TABLES WHERE TABLE_SCHEMA='+quoted+' ORDER BY TABLE_NAME LIMIT 1000'),
            ('foreign_keys',['table','column','referenced_table','referenced_column'],
             'SELECT TABLE_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA='+quoted+' AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY TABLE_NAME,ORDINAL_POSITION LIMIT 5000')):
            extra=command(base+['-e',sql]);rows=[]
            if extra.get('returncode')==0:
                for line in extra['stdout'].splitlines():
                    values=line.split('\t')
                    if len(values)==len(fields):rows.append(dict(zip(fields,values)))
            item[key]={'available':extra.get('returncode')==0,'items':rows,
                       'limit_reached':extra.get('truncated',False) or len(rows)>=(1000 if key=='tables' else 5000)}
        result.append(item)
    return {'available':True,'schemas':result,'requested_schemas':requested or [],
            'scope':'Metadata only; table row counts are estimates. No customer rows, defaults or configuration values queried'}


def text_file(path, limit=8_000_000):
    try:
        with open(path,encoding='utf-8',errors='replace') as f:return f.read(limit)
    except OSError:return ''


def counters(proc=Path('/proc')):
    """One point in time; counters are converted to rates only over an interval."""
    out={'at':time.monotonic(),'cpu':None,'memory':{},'interfaces':{},'processes':{}}
    try:
        parts=[int(n) for n in text_file(proc/'stat').splitlines()[0].split()[1:9]]
        out['cpu']=(sum(parts),parts[3]+parts[4])
    except (ValueError,IndexError):pass
    for line in text_file(proc/'meminfo').splitlines():
        parts=line.split()
        if parts and parts[0] in {'MemTotal:','MemAvailable:','SwapTotal:','SwapFree:'}:
            try:out['memory'][parts[0][:-1]]=int(parts[1])*1024
            except (ValueError,IndexError):pass
    for line in text_file(proc/'net/dev').splitlines()[2:]:
        try:
            name,raw=line.split(':',1);v=raw.split()
            out['interfaces'][name.strip()]={'rx_bytes':int(v[0]),'tx_bytes':int(v[8]),
                'rx_errors':int(v[2]),'rx_dropped':int(v[3]),'tx_errors':int(v[10]),'tx_dropped':int(v[11])}
        except (ValueError,IndexError):pass
    try:entries=list(proc.iterdir())
    except OSError:entries=[]
    page=os.sysconf('SC_PAGE_SIZE')
    for entry in entries:
        if not entry.name.isdigit():continue
        try:
            head,sep,tail=text_file(entry/'stat',16384).rpartition(')')
            if not sep:continue
            _,name=head.split('(',1);v=tail.split()
            start=int(v[19]);pid=int(entry.name)
            out['processes'][(pid,start)]={'pid':pid,'name':safe_path(name[:120]),
                'ticks':int(v[11])+int(v[12]),'rss_bytes':max(0,int(v[21]))*page}
        except (ValueError,IndexError):pass
    return out


def interval_report(before, after, cpu_count=None):
    elapsed=after['at']-before['at'];cpu_count=max(1,cpu_count or os.cpu_count() or 1)
    cpu=None
    if before['cpu'] and after['cpu']:
        total=after['cpu'][0]-before['cpu'][0];idle=after['cpu'][1]-before['cpu'][1]
        if total>0 and 0<=idle<=total:cpu=round(100*(total-idle)/total,2)
    memory=after['memory'].copy();total=memory.get('MemTotal');available=memory.get('MemAvailable')
    memory['used_percent']=round(100*max(0,total-available)/total,2) if total and available is not None else None
    net=[]
    for name,current in sorted(after['interfaces'].items()):
        old=before['interfaces'].get(name);item={'name':safe_path(name),**current,'rx_bps':None,'tx_bps':None}
        if old and elapsed>0 and all(current[k]>=old[k] for k in ('rx_bytes','tx_bytes')):
            item.update({direction+'_bps':round(8*(current[direction+'_bytes']-old[direction+'_bytes'])/elapsed,2) for direction in ('rx','tx')})
        net.append(item)
    processes=[];hz=os.sysconf('SC_CLK_TCK')
    for key,current in after['processes'].items():
        old=before['processes'].get(key);percent=None
        if old and elapsed>0 and current['ticks']>=old['ticks']:
            percent=round(min(100,100*(current['ticks']-old['ticks'])/hz/elapsed/cpu_count),3)
        item={k:v for k,v in current.items() if k!='ticks'}
        item['cpu_percent_total_capacity']=percent;processes.append(item)
    processes.sort(key=lambda p:(-(p['cpu_percent_total_capacity'] or 0),-p['rss_bytes'],p['pid']))
    return {'interval_seconds':round(elapsed,3),'cpu_percent':cpu,'memory_bytes':memory,'interfaces':net,
            'process_count_visible':len(processes),'top_processes':processes[:100],
            'processes_truncated':len(processes)>100}


def connections(proc=Path('/proc')):
    """Per-port TCP/UDP summary, no client IP addresses or remote endpoints."""
    tcp=Counter();listeners=set();udp=set();readable=[];truncated=[]
    for protocol in ('tcp','tcp6','udp','udp6'):
        raw=text_file(proc/'net'/protocol)
        if not raw:continue
        if len(raw)>=8_000_000:truncated.append(protocol)
        readable.append(protocol)
        for line in raw.splitlines()[1:]:
            v=line.split()
            if len(v)<4:continue
            try:port=int(v[1].rsplit(':',1)[1],16)
            except (ValueError,IndexError):continue
            if protocol.startswith('tcp'):
                if v[3]=='0A':listeners.add((protocol,port))
                elif v[3]=='01':tcp[(protocol,port)]+=1
            else:udp.add((protocol,port))
    return {'readable_tables':readable,'tables_truncated':truncated,
        'tcp_listeners':[{'protocol':p,'port':n} for p,n in sorted(listeners)],
        'udp_bound_ports':[{'protocol':p,'port':n} for p,n in sorted(udp)],
        'tcp_established_total':sum(tcp.values()) if any(p.startswith('tcp') for p in readable) else None,
        'tcp_established_by_local_port':[{'protocol':p,'local_port':n,'sockets':count} for (p,n),count in tcp.most_common(200)],
        'ports_truncated':len(tcp)>200,'scope':'TCP sockets are not IPTV viewers; client IPs are omitted. Counts may be partial when tables_truncated is nonempty'}


def disks():
    seen=set();items=[]
    for line in text_file('/proc/mounts').splitlines():
        v=line.split()
        if len(v)<3 or v[2] not in {'ext2','ext3','ext4','xfs','btrfs','zfs','overlay','tmpfs','nfs','nfs4'}:continue
        mount=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m.group(1),8)),v[1])
        if mount.startswith(('/proc','/sys','/dev','/run')) and mount!='/dev/shm':continue
        try:
            st=os.statvfs(mount);dev=os.stat(mount).st_dev
            if dev in seen:continue
            seen.add(dev);total=st.f_blocks*st.f_frsize;used=(st.f_blocks-st.f_bfree)*st.f_frsize
            items.append({'mount':safe_path(mount),'filesystem':v[2],'total_bytes':total,'used_bytes':used,
                'available_bytes':st.f_bavail*st.f_frsize,'used_percent':round(100*used/total,2) if total else None,
                'inodes_total':st.f_files,'inodes_free':st.f_ffree})
        except OSError:continue
        if len(items)>=100:break
    return items


def packages():
    r=command(['dpkg-query','-W','-f=${binary:Package}\t${Version}\t${db:Status-Status}\n'])
    if r.get('returncode')!=0:return {'available':False}
    items=[]
    for line in r['stdout'].splitlines():
        v=line.split('\t')
        if len(v)==3 and v[2]=='installed' and re.search(SERVICE_PATTERN+r'|^python3(?:[.:-]|$)|openssl',v[0],re.I):
            items.append({'package':safe_path(v[0]),'version':v[1][:160]})
    return {'available':True,'items':items[:300],'truncated':len(items)>300 or r.get('truncated',False),
            'scope':'OS package database only; bundled software may have different versions'}


def runtime_layout():
    items=[]
    for root in (Path('/home/xui'),Path('/home/xtreamcodes/iptv_xtream_codes'),Path('/opt/xui')):
        for name in ('service','status','tools','license','update'):
            p=root/name
            try:
                st=p.lstat()
                if not stat.S_ISREG(st.st_mode):continue
                with p.open('rb') as f:head=f.read(256)
                kind='ELF' if head.startswith(b'\x7fELF') else 'PHP' if b'<?php' in head else 'script' if head.startswith(b'#!') else 'unknown'
                items.append({'path':safe_path(p),'size_bytes':st.st_size,'mode':oct(st.st_mode&0o7777),'format':kind})
            except OSError:pass
    return items


def write_report(data, destination):
    """Create a private report; refuse existing files and symlinks."""
    if not destination:
        json.dump(data,sys.stdout,ensure_ascii=False,indent=2);sys.stdout.write('\n');return
    path=Path(destination)
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n')

def main():
    parser=argparse.ArgumentParser(description='Informe de Linux/XUI de solo lectura. Python 3.8+. Sin dependencias.')
    parser.add_argument('--output',help='Archivo JSON nuevo; permisos 600. Si se omite, JSON en stdout.')
    parser.add_argument('--seconds',type=int,choices=range(5,61),default=15,metavar='5..60',help='Duración del muestreo; predeterminado 15 segundos.')
    parser.add_argument('--database',action='append',default=[],help='Nombre de base para leer únicamente su esquema (opcional, repetible).')
    parser.add_argument('--skip-database',action='store_true',help='No consultar MySQL/MariaDB.')
    args=parser.parse_args()
    if any(not re.fullmatch(r'[A-Za-z0-9_$-]{1,64}',name) for name in args.database):parser.error('Nombre de base no válido.')
    if args.output and Path(args.output).exists():parser.error('El archivo de salida ya existe; elige otro nombre.')
    print('Recopilando inventario técnico de solo lectura…',file=sys.stderr)
    data={'report':REPORT_VERSION,'timestamp':dt.datetime.now(dt.timezone.utc).isoformat(),
          'scope':'OS and application metadata, service states, local ports, bounded filenames, sampled resource consumption and database schema. No customer rows, client IPs, passwords, license values, source contents, full configurations, process arguments/environment or logs. Only filtered location directives are extracted from configuration.',
          'system':os_info(),'services':services(),'listening_tcp_ports':listener_ports(),'directories':roots(),
          'web_routing':web_routes(),'runtime_files':runtime_layout(),'packages':packages(),'disks':disks(),
          'database_structure':{'available':False,'reason':'skipped_by_user'} if args.skip_database else database_schema(args.database),
          'limitations':['Snapshot of one server; does not discover load balancers or MikroTik configuration.',
              'TCP sockets do not identify subscribers, channels or sessions.',
              'No GeoIP or client coordinates collected; no external network requests.',
              'Directory and schema inventories are bounded; media/backups/logs are excluded.',
              'Database metadata may be unavailable with local socket authentication.',
              'CPU/process CPU percentages use total logical CPU capacity; RSS can include shared pages.',
              'Interface rates are separate; adding virtual and physical interfaces can double count.',
              'Resource samples cover only the requested observation window, not historical or peak usage.']}
    samples=[];before=counters();remaining=args.seconds
    while remaining:
        step=min(5,remaining);time.sleep(step);after=counters()
        row=interval_report(before,after);row['timestamp']=dt.datetime.now(dt.timezone.utc).isoformat()
        row['connections']=connections();samples.append(row);before=after;remaining-=step
        print('Muestreo: {} / {} s'.format(args.seconds-remaining,args.seconds),file=sys.stderr)
    data['resource_samples']=samples
    data['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat()
    try:write_report(data,args.output)
    except OSError as error:
        print('No se pudo crear el informe: {}'.format(type(error).__name__),file=sys.stderr);return 1
    if args.output:print('Informe guardado en '+args.output,file=sys.stderr)
    return 0

if __name__=='__main__':sys.exit(main())
