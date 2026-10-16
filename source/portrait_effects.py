"""Local landmark-guided additions to the unchanged v2 beauty preset."""
import threading
from pathlib import Path
import cv2
import numpy as np

_lock=threading.Lock()
_models=None


def landmarks(rgb,model_root):
    global _models
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import FaceLandmarker,FaceLandmarkerOptions,PoseLandmarker,PoseLandmarkerOptions
    ratio=min(1,1280/max(rgb.shape[:2]))
    small=cv2.resize(rgb,None,fx=ratio,fy=ratio,interpolation=cv2.INTER_AREA)
    with _lock:
        if _models is None:
            face_path=Path(model_root)/'face_landmarker.task';pose_path=Path(model_root)/'pose_landmarker_lite.task'
            if not face_path.is_file() or not pose_path.is_file():raise RuntimeError('缺少新增美颜模型，请保留完整 assets/models 文件夹。')
            face=FaceLandmarker.create_from_options(FaceLandmarkerOptions(base_options=BaseOptions(model_asset_buffer=face_path.read_bytes()),num_faces=6,min_face_detection_confidence=.6,min_face_presence_confidence=.65))
            try:pose=PoseLandmarker.create_from_options(PoseLandmarkerOptions(base_options=BaseOptions(model_asset_buffer=pose_path.read_bytes()),num_poses=4,min_pose_detection_confidence=.6,min_pose_presence_confidence=.65))
            except Exception:face.close();raise
            _models=(face,pose)
        image=mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(small))
        faces=_models[0].detect(image).face_landmarks
        poses=_models[1].detect(image).pose_landmarks
    h,w=rgb.shape[:2]
    return [np.array([(p.x*w,p.y*h) for p in f],np.float32) for f in faces],poses


def feature_indices():
    from mediapipe.tasks.python.vision.face_landmarker import FaceLandmarksConnections as F
    def indices(name):return sorted({n for c in getattr(F,name) for n in (c.start,c.end)})
    return {name:indices('FACE_LANDMARKS_'+name) for name in ('FACE_OVAL','LEFT_EYE','RIGHT_EYE','LEFT_EYEBROW','RIGHT_EYEBROW','LIPS')}


def face_mask(points,shape,indices):
    mask=np.zeros(shape,np.float32)
    cv2.fillConvexPoly(mask,cv2.convexHull(points[indices['FACE_OVAL']].astype(np.int32)),1)
    for name in ('LEFT_EYE','RIGHT_EYE','LEFT_EYEBROW','RIGHT_EYEBROW','LIPS'):
        region=points[indices[name]];center=region.mean(axis=0)
        cv2.fillConvexPoly(mask,cv2.convexHull((center+(region-center)*1.3).astype(np.int32)),0)
    width=np.ptp(points[indices['FACE_OVAL'],0])
    return cv2.GaussianBlur(mask,(0,0),max(1,width*.008))


def reduce_shine_wrinkles(rgb,mask,face_width):
    """Suppress local skin highlights and dark fine creases, not exposure."""
    source=rgb.astype(np.float32)
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY).astype(np.float32)
    chroma=cv2.cvtColor(rgb,cv2.COLOR_RGB2YCrCb)
    skin=cv2.inRange(chroma,(25,120,65),(255,190,150)).astype(np.float32)/255
    protected=mask*cv2.GaussianBlur(skin,(0,0),max(1,face_width*.005))
    low=cv2.GaussianBlur(source,(0,0),max(2,face_width*.016))
    low_gray=cv2.cvtColor(low,cv2.COLOR_RGB2GRAY)
    crease=np.clip((low_gray-gray-1)/12,0,1)*protected*np.clip((gray-65)/35,0,1)*.55
    result=source+(low-source)*crease[:,:,None]
    local_light=cv2.GaussianBlur(gray,(0,0),max(3,face_width*.055))
    selected=gray[protected>.5]
    if selected.size:
        threshold=max(135,float(np.percentile(selected,65)))
        shine=np.clip((gray-threshold)/35,0,1)*protected
        reduction=np.minimum(np.maximum(gray-local_light-2,0)*.68,20)*shine
        result*=np.maximum(0,(gray-reduction)/np.maximum(gray,1))[:,:,None]
    return np.clip(result,0,255).astype(np.uint8)


def radial_eye_field(mx,my,center,eye_width,amount=.10):
    cx,cy=center;rx=max(4,eye_width*.95);ry=max(4,eye_width*.72)
    h,w=mx.shape;l=max(0,int(cx-rx));r=min(w,int(cx+rx+1));t=max(0,int(cy-ry));b=min(h,int(cy+ry+1))
    if r<=l or b<=t:return
    yy,xx=np.mgrid[t:b,l:r].astype(np.float32)
    radius=(xx-cx)**2/rx**2+(yy-cy)**2/ry**2
    weight=np.maximum(0,1-radius)**2
    mx[t:b,l:r]-=(xx-cx)*amount*weight
    my[t:b,l:r]-=(yy-cy)*amount*weight


def slim_face_field(mx,my,points,indices,amount=.075):
    oval=points[indices['FACE_OVAL']]
    l,t=oval.min(axis=0);r,b=oval.max(axis=0);fw=r-l;fh=b-t
    if fw<40 or fh<50:return False
    eyes=[points[indices[name]].mean(axis=0) for name in ('LEFT_EYE','RIGHT_EYE')]
    eye_mid=(eyes[0]+eyes[1])/2
    # Avoid reshaping heavily turned or partially clipped faces.
    if abs(points[1,0]-eye_mid[0])>fw*.22 or l<1 or r>=mx.shape[1]-1:return False
    cx=(l+r)/2;cy=t+fh*.65;rx=fw*.72;ry=fh*.57
    x0=max(0,int(cx-rx));x1=min(mx.shape[1],int(cx+rx+1));y0=max(0,int(cy-ry));y1=min(mx.shape[0],int(cy+ry+1))
    yy,xx=np.mgrid[y0:y1,x0:x1].astype(np.float32)
    u=(xx-cx)/rx;v=(yy-cy)/ry
    horizontal=np.maximum(0,1-u*u)**2
    vertical=np.maximum(0,1-v*v)**2
    # The support extends beyond the jaw and smoothly reaches zero: no seams.
    mx[y0:y1,x0:x1]+=(xx-cx)*amount*horizontal*vertical*2.4
    return True


def valid_bodies(poses,width,height):
    candidates=[]
    for pose in poses:
        if len(pose)<25:continue
        joints=[pose[i] for i in (11,12,23,24)]
        if any((p.visibility or 0)<.7 or (p.presence or 0)<.7 or not .02<p.x<.98 or not .02<p.y<.98 for p in joints):continue
        ls,rs,lh,rh=[np.array((p.x*width,p.y*height),np.float32) for p in joints]
        shoulder=(ls+rs)/2;hip=(lh+rh)/2;length=np.linalg.norm(hip-shoulder);span=np.linalg.norm(ls-rs)
        if length<50 or span<50 or hip[1]-shoulder[1]<length*.7:continue
        center=shoulder*.35+hip*.65
        candidates.append((center,max(span,np.linalg.norm(lh-rh))*1.05,length*.85))
    # Skip overlapping people rather than stretching an adjacent subject.
    return [a for i,a in enumerate(candidates) if not any(i!=j and abs(a[0][0]-b[0][0])<(a[1]+b[1])*.65 and abs(a[0][1]-b[0][1])<(a[2]+b[2])*.65 for j,b in enumerate(candidates))]


def slim_body_field(mx,my,body,amount=.032):
    (cx,cy),rx,ry=body
    l=max(0,int(cx-rx));r=min(mx.shape[1],int(cx+rx+1));t=max(0,int(cy-ry));b=min(mx.shape[0],int(cy+ry+1))
    yy,xx=np.mgrid[t:b,l:r].astype(np.float32)
    weight=np.maximum(0,1-((xx-cx)/rx)**2)**2*np.maximum(0,1-((yy-cy)/ry)**2)**2
    mx[t:b,l:r]+=(xx-cx)*amount*weight*1.8


def apply_portrait_effects(original,base,model_root):
    source=np.asarray(original);rgb=np.asarray(base).copy()
    faces,poses=landmarks(source,model_root);indices=feature_indices()
    h,w=rgb.shape[:2];my,mx=np.mgrid[0:h,0:w].astype(np.float32)
    slim_faces=0;eyes_count=0
    for points in faces:
        oval=points[indices['FACE_OVAL']];left,top=oval.min(axis=0);right,bottom=oval.max(axis=0)
        if right-left<40:continue
        l=max(0,int(left)-8);r=min(w,int(right)+9);t=max(0,int(top)-8);b=min(h,int(bottom)+9)
        if r<=l or b<=t:continue
        local=points-np.array((l,t),np.float32)
        mask=face_mask(local,(b-t,r-l),indices)
        rgb[t:b,l:r]=reduce_shine_wrinkles(rgb[t:b,l:r],mask,right-left)
        slim_faces+=int(slim_face_field(mx,my,points,indices))
        for name in ('LEFT_EYE','RIGHT_EYE'):
            eye=points[indices[name]];eye_width=np.linalg.norm(eye.max(axis=0)-eye.min(axis=0))
            if eye_width<8:continue
            radial_eye_field(mx,my,eye.mean(axis=0),eye_width);eyes_count+=1
    bodies=valid_bodies(poses,w,h)
    for body in bodies:slim_body_field(mx,my,body)
    result=cv2.remap(rgb,mx,my,cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT_101)
    return result,{'landmark_faces':len(faces),'eye_regions':eyes_count,'slim_faces':slim_faces,'slim_bodies':len(bodies),'extra_effects':'大眼、瘦脸、局部去油光、淡化细纹、轻微瘦身'}
