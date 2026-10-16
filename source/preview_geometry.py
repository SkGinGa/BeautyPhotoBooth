"""One transform shared by live rendering, touch AF, and camera AF rectangles."""
class PreviewTransform:
    def __init__(self, image_size, panel_size, aspect, mirror=True, coord=None, visible=None,centering=(.5,.42),zoom=1):
        iw,ih=image_size;pw,ph=panel_size
        cw=min(iw,ih*aspect)/zoom;ch=cw/aspect
        left=(iw-cw)*centering[0];top=(ih-ch)*centering[1]
        self.crop=(left,top,left+cw,top+ch)
        factor=min(max(1,pw-12)/cw,max(1,ph-12)/ch)
        dw=max(1,round(cw*factor));dh=max(1,round(ch*factor))
        self.display=((pw-dw)/2,(ph-dh)/2,dw,dh)
        self.image_size=image_size;self.mirror=mirror
        self.coord=coord if coord and min(coord)>0 else image_size
        self.visible=visible if visible and min(visible[2:])>0 else (0,0,*self.coord)

    def to_camera(self,x,y):
        dx,dy,dw,dh=self.display
        if not dx<=x<=dx+dw or not dy<=y<=dy+dh:return None
        u=(x-dx)/dw;v=(y-dy)/dh
        if self.mirror:u=1-u
        l,t,r,b=self.crop;iw,ih=self.image_size;vx,vy,vw,vh=self.visible;cw,ch=self.coord
        return ((vx+(l+u*(r-l))/iw*vw)/cw,(vy+(t+v*(b-t))/ih*vh)/ch)

    def to_canvas(self,x,y):
        dx,dy,dw,dh=self.display;l,t,r,b=self.crop;iw,ih=self.image_size
        vx,vy,vw,vh=self.visible;cw,ch=self.coord
        u=((x*cw-vx)/vw*iw-l)/(r-l);v=((y*ch-vy)/vh*ih-t)/(b-t)
        if self.mirror:u=1-u
        return dx+u*dw,dy+v*dh

    def box_on_canvas(self,box):
        x,y,w,h=box;a,b=self.to_canvas(x,y);c,d=self.to_canvas(x+w,y+h)
        dx,dy,dw,dh=self.display
        left=max(dx,min(a,c));right=min(dx+dw,max(a,c));top=max(dy,min(b,d));bottom=min(dy+dh,max(b,d))
        return (left,top,right,bottom) if right>left and bottom>top else None
