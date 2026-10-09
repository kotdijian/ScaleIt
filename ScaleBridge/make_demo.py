"""Create synthetic images and a 100 mm scale; never represents measured accuracy."""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from PySide6.QtGui import QImage,QPainter,QColor,QPen,QFont,QGuiApplication
from core import Model,write_json,VERSION

def create_demo(destination):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Choose a new demo directory.')
    sparse=destination/'sparse'; images=destination/'images'; sparse.mkdir(parents=True); images.mkdir()
    (sparse/'cameras.txt').write_text('1 PINHOLE 1600 1200 1200 1190 800 600\n')
    lines=[]; centers=[[-2,0,0],[2,0,0],[0,1,0],[0,-1,0]]
    for i,c in enumerate(centers,1):
        lines.extend([f'{i} 1 0 0 0 {-c[0]} {-c[1]} 0 1 image_{i}.png',''])
    (sparse/'images.txt').write_text('\n'.join(lines)+'\n'); (sparse/'points3D.txt').write_text('')
    model=Model(sparse); points={'A':np.array([0,0,5]),'B':np.array([1,0,5]),'C':np.array([0,.8,5])}
    observations={p:{n:model.project(n,xyz).tolist() for n in model.images} for p,xyz in points.items()}
    root=ET.Element('document',version='2.0.0'); chunk=ET.SubElement(root,'chunk',id='0')
    sensors=ET.SubElement(chunk,'sensors'); sensor=ET.SubElement(sensors,'sensor',id='0',label='Demo camera',type='frame')
    ET.SubElement(sensor,'resolution',width='1600',height='1200')
    cal=ET.SubElement(sensor,'calibration',type='frame',attrib={'class':'adjusted'})
    ET.SubElement(cal,'resolution',width='1600',height='1200')
    for tag,value in [('f','1190'),('b1','10'),('b2','0'),('cx','0'),('cy','0'),('k1','0'),('k2','0'),('k3','0'),('k4','0'),('p1','0'),('p2','0')]:
        ET.SubElement(cal,tag).text=value
    cameras=ET.SubElement(chunk,'cameras')
    for i,n in enumerate(model.images):
        camera=ET.SubElement(cameras,'camera',id=str(i),label=n,sensor_id='0',enabled='true')
        M=np.eye(4); M[:3,3]=model.center(n)/2
        ET.SubElement(camera,'transform').text=' '.join(map(str,M.flat)); ET.SubElement(camera,'orientation').text='1'
        image=QImage(1600,1200,QImage.Format.Format_RGB32); image.fill(QColor('#e5dfd4'))
        painter=QPainter(image); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont('Sans Serif',24)); painter.setPen(QColor('#444444'))
        painter.drawText(70,90,'SYNTHETIC DEMO — not real photographs')
        painter.drawText(70,140,f'{n}   A–B: 100 mm   A–C: 80 mm')
        a=observations['A'][n]; b=observations['B'][n]; c=observations['C'][n]
        painter.fillRect(int(a[0])-20,int(a[1])-35,int(b[0]-a[0])+40,75,QColor('#ffffff'))
        painter.setPen(QPen(QColor('#333333'),2))
        for tick in range(11):
            xy=model.project(n,np.array([tick/10,0,5]))
            painter.drawLine(int(xy[0]),int(xy[1]),int(xy[0]),int(xy[1])+20)
        painter.drawLine(int(a[0]),int(a[1]),int(b[0]),int(b[1]))
        painter.setPen(QPen(QColor('#339977'),3)); painter.drawLine(int(a[0]),int(a[1]),int(c[0]),int(c[1]))
        for p in points:
            x,y=observations[p][n]; painter.setPen(QPen(QColor('#bb2222'),2))
            painter.drawEllipse(int(x)-4,int(y)-4,8,8); painter.drawText(int(x)+12,int(y)-12,p)
        painter.end(); image.save(str(images/n))
    xml=destination/'original_cameras.xml'; ET.indent(root); ET.ElementTree(root).write(xml,encoding='utf-8',xml_declaration=True)
    data={'version':VERSION,'paths':{'model':str(sparse),'images':str(images),'xml':str(xml)},
          'model_fingerprint':model.signature,'observations':observations,
          'bars':[{'a':'A','b':'B','length_m':.1,'role':'scale'},{'a':'A','b':'C','length_m':.08,'role':'check'}],
          'min_angle_deg':1,'max_error_px':10}
    write_json(destination/'complete_session.json',data)
    write_json(destination/'blank_session.json',{**data,'observations':{}})
    return data

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('destination',nargs='?',default='demo')
    args=p.parse_args(); app=QGuiApplication([]); create_demo(args.destination); print('Demo created:',args.destination)
