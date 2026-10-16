"""Bounded template geometry and per-photo crop placement."""
from PIL import Image

def move_box(box,dx,dy,canvas):
    x,y,w,h=box;cw,ch=canvas
    return [round(max(0,min(cw-w,x+dx))),round(max(0,min(ch-h,y+dy))),w,h]

def resize_box(box,right,bottom,canvas):
    x,y,w,h=box;cw,ch=canvas
    return [x,y,round(max(20,min(cw-x,right-x))),round(max(20,min(ch-y,bottom-y)))]

def crop_photo(photo,size,centering=(.5,.42),zoom=1):
    w,h=size;cx,cy=centering;zoom=float(zoom)
    if not (1<=zoom<=3 and 0<=cx<=1 and 0<=cy<=1):raise ValueError('照片位置或缩放无效')
    ratio=w/h;cw=min(photo.width,photo.height*ratio)/zoom;ch=cw/ratio
    left=(photo.width-cw)*cx;top=(photo.height-ch)*cy
    return photo.resize((w,h),Image.Resampling.LANCZOS,box=(left,top,left+cw,top+ch))
