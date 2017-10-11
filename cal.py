#!/usr/bin/python2

import sys
import numpy as np
import numpysane as nps
import gnuplotlib as gp
import cv2
import re
import glob
import cPickle as pickle

sys.path[:0] = ('/home/dima/src_boats/stereo-server/analyses',)
import camera_models

sys.path[:0] = ('build/lib.linux-x86_64-2.7/',)
import mrcal
import utils








from IPython.core import ultratb
sys.excepthook = ultratb.FormattedTB(mode='Plain',
                                     color_scheme='Linux', call_pdb=1)








# stuff the user may want to set
pair_want   = 1



read_cache_dots = False

focal_estimate    = 1970 # pixels
imager_w_estimate = 3904
Nwant             = 10





np.set_printoptions(linewidth=1e10) # no line breaks

#datafile_asciilog='viet_norfolk_joint.asciilog'
#datafile_asciilog='/home/dima/data/cal-2017-09-01-drydock/big.sorted.asciilog'
# < /home/dima/data/cal-2017-09-01-drydock/big.asciilog perl -ne '($cam,$pair,$frame) = m{^.*?/input-(left|right)-([01])-0*([0-9]+?)\.jpg}; ($frameprefix) = m{/frames([0-9]+)/}; $frame = $frameprefix*1000000 + $frame if defined $frameprefix; if( !defined $cam ) {print;} else { if($cam eq "left") {$cam=0;} else {$cam=1;} $not_header_marker = /jpg -/ ? "1zzz" : "0zzz"; print "$frame $pair $cam $not_header_marker $_";}' | sort | sed 's/.*zzz //g' > /home/dima/data/cal-2017-09-01-drydock/big.sorted.asciilog








#datafile_asciilog='viet_norfolk_joint.asciilog'

if Nwant == 10:
    datafile_asciilog='/tmp/seahunter_code/cal-2017-09-01-drydock/big.sorted.asciilog'
elif Nwant == 2:
    datafile_asciilog='/tmp/seahunter_code/cal-2017-09-01-drydock/big.sorted.2x2.reordered.asciilog'




# < /home/dima/data/cal-2017-09-01-drydock/big.asciilog perl -ne '($cam,$pair,$frame) = m{^.*?/input-(left|right)-([01])-0*([0-9]+?)\.jpg}; ($frameprefix) = m{/frames([0-9]+)/}; $frame = $frameprefix*1000000 + $frame if defined $frameprefix; if( !defined $cam ) {print;} else { if($cam eq "left") {$cam=0;} else {$cam=1;} $not_header_marker = /jpg -/ ? "1zzz" : "0zzz"; print "$frame $pair $cam $not_header_marker $_";}' | sort | sed 's/.*zzz //g' > /home/dima/data/cal-2017-09-01-drydock/big.sorted.asciilog

# if defined, we will use this:
# datadir_stcal = '/home/dima/data/cal_data_2017_07_14/lfc4/' # for old dot files











re_f = '[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
re_u = '\d+'
re_d = '[-+]?\d+'
re_s = '.+'



def estimate_local_calobject_poses( indices_frame_camera, \
                                    dots, dot_spacing, focal, imager_size ):
    r"""Estimates pose of observed object in a single-camera view

Given observations, and an estimate of camera intrinsics (focal lengths, imager
size) computes an estimate of the pose of the calibration object in respect to
the camera for each frame. This assumes that all frames are independent and all
cameras are independent. This assumes a pinhole camera.

This function is a wrapper around the solvePnP() openCV call, which does all the
work.

The observations are given in a numpy array with axes:

  (iframe, idot_x, idot_y, idot2d_xy)

So as an example, the observed pixel coord of the dot (3,4) in frame index 5 is
the 2-vector dots[5,3,4,:]

Missing observations are given as negative pixel coords.

This function returns an (Nobservations,4,3) array, with the observations
aligned with the dots and indices_frame_camera arrays. Each observation slice is
(4,3) in glue(R, t, axis=-2)

    """

    Nobservations = indices_frame_camera.shape[0]

    # this wastes memory, but makes it easier to keep track of which data goes
    # with what
    Rt_all = np.zeros( (Nobservations, 4, 3), dtype=float)
    camera_matrix = np.array((( focal[0], 0,        imager_size[0]/2), \
                              (        0, focal[1], imager_size[1]/2), \
                              (        0,        0,                 1)))

    full_object = utils.get_full_object(Nwant, Nwant, dot_spacing)

    for i_observation in xrange(dots.shape[0]):
        d = dots[i_observation, ...]

        d = nps.clump( nps.glue(d, full_object, axis=-1), n=2)
        # d is (Nwant*Nwant,5); each row is an xy pixel observation followed by the xyz
        # coord of the point in the calibration object. I pick off those rows
        # where the observations are both >= 0. Result should be (N,5) where N
        # <= Nwant*Nwant
        i = (d[..., 0] >= 0) * (d[..., 1] >= 0)
        d = d[i,:]

        observations = d[:,:2]
        ref_object   = d[:,2:]
        result,rvec,tvec = cv2.solvePnP(ref_object  [..., np.newaxis],
                                        observations[..., np.newaxis],
                                        camera_matrix, None)
        if not result:
            raise Exception("solvePnP failed!")
        if tvec[2] <= 0:
            raise Exception("solvePnP says that tvec.z <= 0. Maybe needs a flip, but please examine this")

        Rt_all[i_observation, :3, :] = utils.Rodrigues_toR_broadcasted(rvec.ravel())
        Rt_all[i_observation,  3, :] = tvec.ravel()

    return Rt_all

def estimate_camera_poses( calobject_poses_Rt, indices_frame_camera, \
                           dots, dot_spacing, Ncameras):
    r'''Estimates camera poses in respect to each other

We are given poses of the calibration object in respect to each observing
camera. We also have multiple cameras observing the same calibration object at
the same time, and we have local poses for each. We can thus compute the
relative camera pose from these observations.

We have many frames that have different observations from the same set of
fixed-relative-pose cameras, so we compute the relative camera pose to optimize
the observations

    '''

    # This is a bit of a hack. I look at the correspondence of camera0 to camera
    # i for i in 1:N-1. I ignore all correspondences between cameras i,j if i!=0
    # and j!=0. Good enough for now
    full_object = utils.get_full_object(Nwant, Nwant, dot_spacing)
    Rt = np.array(())


    for i_camera in xrange(1,Ncameras):
        A = np.array(())
        B = np.array(())

        # I traverse my observation list, and pick out observations from frames
        # that had data from both camera 0 and camera i
        i_frame_last = -1
        d0  = None
        d1  = None
        Rt0 = None
        Rt1 = None
        for i_observation in xrange(dots.shape[0]):
            i_frame_this,i_camera_this = indices_frame_camera[i_observation, ...]
            if i_frame_this != i_frame_last:
                d0  = None
                d1  = None
                Rt0 = None
                Rt1 = None
                i_frame_last = i_frame_this

            if i_camera_this == 0:
                if Rt0 is not None:
                    raise Exception("Saw multiple camera0 observations in frame {}".format(i_frame_this))
                Rt0 = calobject_poses_Rt[i_observation, ...]
                d0  = dots[i_observation, ...]
            if i_camera_this == i_camera:
                if Rt1 is not None:
                    raise Exception("Saw multiple camera{} observations in frame {}".format(i_camera_this,
                                                                                            i_frame_this))
                Rt1 = calobject_poses_Rt[i_observation, ...]
                d1  = dots[i_observation, ...]

                if Rt0 is None: # have camera1 observation, but not camera0
                    continue


                # d looks at one frame and has shape (Nwant,Nwant,7). Each row is
                #   xy pixel observation in left camera
                #   xy pixel observation in right camera
                #   xyz coord of dot in the calibration object coord system
                d = nps.glue( d0, d1, full_object, axis=-1 )

                # squash dims so that d is (Nwant*Nwant,7)
                d = nps.clump(d, n=2)

                ref_object = nps.clump(full_object, n=2)

                # # It's possible that I could have incomplete views of the
                # # calibration object, so I pull out only those point
                # # observations that have a complete view. In reality, I
                # # currently don't accept any incomplete views, and much outside
                # # code would need an update to support that. This doesn't hurt, however

                # # d looks at one frame and has shape (10,10,7). Each row is
                # #   xy pixel observation in left camera
                # #   xy pixel observation in right camera
                # #   xyz coord of dot in the calibration object coord system
                # d = nps.glue( d0, d1, full_object, axis=-1 )

                # # squash dims so that d is (100,7)
                # d = nps.transpose(nps.clump(nps.mv(d, -1, -3), n=2))

                # # I pick out those points that have observations in both frames
                # i = (d[..., 0] >= 0) * (d[..., 1] >= 0) * (d[..., 2] >= 0) * (d[..., 3] >= 0)
                # d = d[i,:]

                # # ref_object is (N,3)
                # ref_object = d[:,4:]

                A = nps.glue(A, nps.matmult( ref_object, nps.transpose(Rt0[:3,:])) + Rt0[3,:],
                             axis = -2)
                B = nps.glue(B, nps.matmult( ref_object, nps.transpose(Rt1[:3,:])) + Rt1[3,:],
                             axis = -2)

        Rt = nps.glue(Rt, utils.align3d_procrustes(A, B),
                      axis=-3)

    return Rt

def estimate_frame_poses(calobject_poses_Rt, camera_poses_Rt, indices_frame_camera, dot_spacing):
    r'''We're given

calobject_poses_Rt:

  an array of dimensions (Nobservations,4,3) that contains a
  calobject-to-camera transformation estimate, for each observation of the board

camera_poses_Rt:

  an array of dimensions (Ncameras-1,4,3) that contains a camerai-to-camera0
  transformation estimate. camera0-to-camera0 is the identity, so this isn't
  stored

indices_frame_camera:

  an array of shape (Nobservations,2) that indicates which frame and which
  camera has observed the board

With this data, I return an array of shape (Nframes,6) that contains an estimate
of the pose of each frame, in the camera0 coord system. Each row is (r,t) where
r is a Rodrigues rotation and t is a translation that map points in the
calobject coord system to that of camera 0

    '''


    def process(i_observation0, i_observation1):
        R'''Given a range of observations corresponding to the same frame, estimate the
frame pose'''

        def get_camera0Tboard(i_observation):
            i_frame,i_camera = indices_frame_camera[i_observation, ...]

            Rf = calobject_poses_Rt[i_observation, :3, :]
            tf = calobject_poses_Rt[i_observation,  3, :]
            if i_camera == 0:
                return Rf,tf

            # cameraiTcamera0 camera0Tboard = cameraiTboard
            # I need camera0Tboard = inv(cameraiTcamera0) cameraiTboard
            # camera_poses_Rt    is inv(cameraiTcamera0)
            # calobject_poses_Rt is cameraiTboard
            Rtcam = camera_poses_Rt[i_camera-1, ...]
            Rcam  = Rtcam[:3,:]
            tcam  = Rtcam[ 3,:]

            # Rcam( Rframe *x + tframe) + tcam = Rcam Rframe x + Rcam tframe + tcam
            R = nps.matmult(Rcam, Rf)
            t = nps.matmult( Rcam, nps.transpose(tf)).ravel() + tcam
            return R,t


        # frame poses should map FROM the frame coord system TO the ref coord
        # system (camera 0).

        # special case: if there's a single observation, I just use it
        if i_observation1 - i_observation0 == 1:
            R,t = get_camera0Tboard(i_observation0)
            return nps.glue( utils.Rodrigues_tor_broadcasted(R),
                             t,
                             axis=-1 )

        # Multiple cameras have observed the object for this frame. I have an
        # estimate of these for each camera. I merge them in a lame way: I
        # average out the positions of each point, and fit the calibration
        # object into the mean point cloud
        obj = utils.get_full_object(Nwant, Nwant, dot_spacing)

        sum_obj_unproj = obj*0
        for i_observation in xrange(i_observation0, i_observation1):
            R,t = get_camera0Tboard(i_observation)

            sum_obj_unproj += (nps.matmult(R, nps.dummy(obj, -1)) + nps.transpose(t))[..., 0]

        mean = sum_obj_unproj / (i_observation1 - i_observation0)

        # Got my point cloud. fit

        # transform both to shape = (N*N, 3)
        obj  = nps.clump(obj,  n=2)
        mean = nps.clump(mean, n=2)
        Rt = utils.align3d_procrustes( mean, obj )
        R = Rt[:3,:]
        t = Rt[3 ,:]
        return nps.glue( utils.Rodrigues_tor_broadcasted(R),
                         t,
                         axis=-1 )





    frame_poses_rt = np.array(())

    i_frame_current          = -1
    i_observation_framestart = -1;

    for i_observation in xrange(indices_frame_camera.shape[0]):
        i_frame,i_camera = indices_frame_camera[i_observation, ...]

        if i_frame != i_frame_current:
            if i_observation_framestart >= 0:
                rt = process(i_observation_framestart, i_observation)
                frame_poses_rt = nps.glue(frame_poses_rt, rt, axis=-2)

            i_observation_framestart = i_observation
            i_frame_current = i_frame

    if i_observation_framestart >= 0:
        rt = process(i_observation_framestart, indices_frame_camera.shape[0])
        frame_poses_rt = nps.glue(frame_poses_rt, rt, axis=-2)

    return frame_poses_rt

def make_seed(inputs):
    r'''Generate a solution seed for a given input'''


    def make_intrinsics_vector(i_camera, inputs):
        imager_w,imager_h = inputs['imager_size']
        return np.array( (inputs['focal_estimate'][0], inputs['focal_estimate'][1],
                          float(imager_w-1)/2.,
                          float(imager_h-1)/2.))




    intrinsics = nps.cat( *[make_intrinsics_vector(i_camera, inputs) \
                            for i_camera in xrange(inputs['Ncameras'])] )

    # I compute an estimate of the poses of the calibration object in the local
    # coord system of each camera for each frame. This is done for each frame
    # and for each camera separately. This isn't meant to be precise, and is
    # only used for seeding.
    #
    # I get rotation, translation in a (4,3) array, such that R*calobject + t
    # produces the calibration object points in the coord system of the camera.
    # The result has dimensions (N,4,3)
    calobject_poses_Rt = \
        estimate_local_calobject_poses( inputs['indices_frame_camera'],
                                        inputs['dots'],
                                        inputs['dot_spacing'],
                                        inputs['focal_estimate'],
                                        inputs['imager_size'] )
    # these map FROM the coord system of the calibration object TO the coord
    # system of this camera

    # I now have a rough estimate of calobject poses in the coord system of each
    # frame. One can think of these as two sets of point clouds, each attached to
    # their camera. I can move around the two sets of point clouds to try to match
    # them up, and this will give me an estimate of the relative pose of the two
    # cameras in respect to each other. I need to set up the correspondences, and
    # align3d_procrustes() does the rest
    #
    # I get transformations that map points in 1-Nth camera coord system to 0th
    # camera coord system. R,t have dimensions (N-1,3,3) and (N-1,3) respectively
    camera_poses_Rt = estimate_camera_poses( calobject_poses_Rt,
                                             inputs['indices_frame_camera'],
                                             inputs['dots'],
                                             inputs['dot_spacing'],
                                             inputs['Ncameras'] )

    if len(camera_poses_Rt):
        # extrinsics should map FROM the ref coord system TO the coord system of the
        # camera in question. This is backwards from what I have. To flip:
        #
        # R*x + t = x'    ->     x = Rt x' - Rt t
        R = camera_poses_Rt[..., :3, :]
        t = camera_poses_Rt[...,  3, :]
        extrinsics = nps.atleast_dims( nps.glue( utils.Rodrigues_tor_broadcasted(nps.transpose(R)),
                                                 -nps.matmult( nps.dummy(t,-2), R )[..., 0, :],
                                                 axis=-1 ),
                                       -2)
    else:
        extrinsics = np.zeros((0,6))

    frames = \
        estimate_frame_poses(calobject_poses_Rt, camera_poses_Rt,
                             inputs['indices_frame_camera'],
                             inputs['dot_spacing'])
    return intrinsics,extrinsics,frames

def solve_monocular(inputs, index=None):

    intrinsics,extrinsics,frames = make_seed(inputs)
    observations = inputs['dots']

    # done with everything. Run the calibration, in several passes.
    projected = \
        utils.project_points(intrinsics, extrinsics, frames,
                             inputs['dot_spacing'], Nwant)
    err = utils.compute_reproj_error(projected, observations,
                                     inputs['indices_frame_camera'], Nwant)

    norm2_err = nps.inner(err.ravel(), err.ravel())
    rms_err   = np.sqrt( norm2_err / (err.ravel().shape[0]/2) )
    print "initial norm2 err: {}, rms: {}".format(norm2_err, rms_err )



    observations_point          = np.zeros((0,3), dtype=float)
    indices_point_camera_points = np.zeros((0,2), dtype=np.int32)
    points                      = np.zeros((0,3), dtype=float)

    # distortion_model = "DISTORTION_NONE"
    # mrcal.optimize(intrinsics, extrinsics, frames, points,
    #                observations, inputs['indices_frame_camera'],
    #                observations_point, indices_point_camera_points,
    #                distortion_model, False, False )

    # distortion_model = "DISTORTION_NONE"
    # mrcal.optimize(intrinsics, extrinsics, frames, points,
    #                observations, inputs['indices_frame_camera'],
    #                observations_point, indices_point_camera_points,
    #                distortion_model, True, False )


    distortion_model = "DISTORTION_CAHVOR"
    Ndistortions = mrcal.getNdistortionParams(distortion_model)
    intrinsics = nps.glue( intrinsics, np.random.random((inputs['Ncameras'], Ndistortions))*1e-4, axis=-1 )

    mrcal.optimize(intrinsics, extrinsics, frames, points,
                   observations, inputs['indices_frame_camera'],
                   observations_point, indices_point_camera_points,
                   distortion_model, False, True)

    # skip = [0,  21, 717, 718, 719, 720, 721, 722, 723, 724] # 0-0
    # skip = [0, 816, 817, 822]                               # 0-1
    if index == 0:
        skip = [0, 818, 819, 820, 821, 822, 823, 824, 825, 826] # 1-0
    elif index == 1:
        skip = [468, 469, 705, 706] # 1-1
    else:
        skip = None

    if skip:
        print "culling worst"
        mrcal.optimize(intrinsics, extrinsics, frames, points,
                       observations, inputs['indices_frame_camera'],
                       observations_point, indices_point_camera_points,
                       distortion_model, False, True,
                       skip, None)
    return intrinsics,frames

def _read_dots_stcal(datadir):

    raise Exception("This is still coded to assume stereo PAIRS. Update to use discrete cameras")
    def read_observations_from_file__old_dot(filename, which):
        r"""Parses the xxx.dots from the old stcal tool

        Given a xxx.dots file produced with stcal, read the observations into a
        numpy array. Returns this numpy array and a list of metadata.

        The array has axes: (iframe, idot_y, idot_x, idot2d_xy)

        So as an example, the observed pixel coord of the dot (3,4) in frame
        index 5 is the 2-vector dots[5,3,4,:]

        The metadata is a dictionary, containing the dimensions of the imager,
        and the indices of frames that the numpy array contains

        """

        with open(filename, 'r') as f:
            for l in f:
                if re.match('# Format: jplv$',
                            l):
                    break
            else:
                raise Exception('No explicit "Format: jplv" when reading {}'.format(filename))

            # Data. Axes: (iframe, idot_y, idot_x, idot2d_xy)
            # So the observed pixel coord of the dot (3,4) in frame index 5 is
            # the 2-vector dots[5,3,4,:]
            dots       = np.array( (), dtype=float)
            inputs   = {'frames': [],
                          'imager_size': None,
                          'dot_spacing': None,
                          'Nwant'      : Nwant}
            cur_frame  = None
            cur_iframe = None

            for l in f:
                if l[0] == '\n' or l[0] == '#':
                    continue

                m = re.match('IMAGE ({u}) ({u})'.format(u=re_u),
                             l)
                if m:
                    if inputs['imager_size'] is not None:
                        raise Exception('Got line "{}", but already have width, height'.format(l))
                    inputs['imager_size'] = (int(m.group(1)), int(m.group(2)))
                    continue

                m = re.match('DOT ({s}) (stcal-({u})-({s}).pnm) FIX ({u}) ({u}) ({u}) ({f}) ({f}) IMG ({f}) ({f})'.format(f=re_f, u=re_u, d=re_d, s=re_s), l)
                if m:
                    if which != m.group(1):
                        raise Exception("Reading file {}: expected '{}' frames, but got '{}". \
                                        format(filename, which, m.group(1)))
                    if which != m.group(4):
                        raise Exception("Reading file {}: expected '{}' frames, but got image file '{}". \
                                        format(filename, which, m.group(2)))
                    frame  = int(m.group(3))
                    iframe = int(m.group(5))
                    idot   = (int(   m.group(6)),  int(  m.group(7)))
                    dot3d  = (float( m.group(8)),  float(m.group(9)))
                    dot2d  = np.array(( float(m.group(10)), float(m.group(11))))

                    if cur_iframe == iframe and \
                       cur_frame  != frame:
                        raise Exception('frame changed, but iframe did not')
                    if cur_frame  == frame and \
                       cur_iframe != iframe:
                        raise Exception('iframe changed, but frame did not')
                    if cur_iframe is None and iframe != 0:
                        raise Exception('iframe should start at 0')

                    if cur_iframe != iframe:
                        if cur_iframe is not None and cur_iframe+1 != iframe:
                            raise Exception('non-consecutive frame index...')
                        if cur_frame is not None and cur_frame >= frame:
                            raise Exception('non-increasing frame number...')

                        cur_frame,cur_iframe = frame,iframe
                        dots = nps.glue( dots,
                                         np.zeros((Nwant,Nwant,2), dtype=float) - 1,
                                         axis=-4 )
                        inputs['frames'].append(frame)

                    dot_spacing = np.array(dot3d, dtype=float) / np.array(idot, dtype=float)
                    if inputs['dot_spacing'] is None:
                        inputs['dot_spacing'] = dot_spacing
                    else:
                        if np.max( np.abs(inputs['dot_spacing'] - dot_spacing) ) > 1e-4:
                            raise Exception("Inconsistent dot spacing. Previously saw {} but now have {}". \
                                            format(inputs['dot_spacing'], dot_spacing))

                    dots[-1, idot[1]-1, idot[0]-1,:] = dot2d
                    continue

                raise Exception('Got unknown line "{}"'.format(l))

        return dots, inputs




    dots            = {}
    inputs        = {'Nwant': Nwant}

    for fil in ('{}/stcal-left.dots' .format(datadir),
                '{}/stcal-right.dots'.format(datadir)):
        m = re.match( '.*-(left|right)\.dots$', fil)
        if not m: raise Exception("Can't tell if '{}' is left or right".format(fil))
        which = m.group(1)

        d,m             = read_observations_from_file__old_dot( fil, which )
        dots    [which] = d
        inputs[which] = m

    return dots,inputs

def _read_dots_asciilog(datafile):
    r'''Reads an asciilog dots file produced by cdas-find-dots

    cdas-find-dots lives in the cdas-core project.

    This function parses a single data file that contains ALL the observations.
    There are no assumptions of any joint observations. I.e. during each instant
    in time anywhere from 1 to N>2 cameras could have been observed (this is a
    generic N-camera calibration, NOT a camera pair calibration).

    Each board observation lives in a slice of the returned 'dots' array. The
    frame and camera indices responsible for that observation live in a
    corresponding slice of the inputs['indices_frame_camera'] array

    '''
    def get_next_dots(f):

        def parse_image_header(l):
            # Two image formats are possible:
            #   frame00002-pair1-cam0.jpg
            #   input-right-0-02093.jpg
            m = re.match('([^ ]*/frame([0-9]+)-pair([01])-cam([0-9]+)\.[a-z][a-z][a-z]) ({f}) ({f}) {Nwant} {Nwant} ({d}) - - - - - -\n$'.format(f=re_f, d=re_d, Nwant=Nwant), l)
            if m:
                path        = m.group(1)
                i_frame     = int(m.group(2))
                i_pair      = int(m.group(3))
                i_camera    = int(m.group(4))
                dot_spacing = float(m.group(6))
                Ndetected   = int(m.group(7))
                return path,i_frame,i_pair,i_camera,dot_spacing,Ndetected
            m = re.match('([^ ]*/input-(left|right)-([01])-([0-9]+)\.[a-z][a-z][a-z]) ({f}) ({f}) {Nwant} {Nwant} ({d}) - - - - - -\n$'.format(f=re_f, d=re_d,Nwant=Nwant), l)
            if m:
                path        = m.group(1)
                i_frame     = int(m.group(4))
                i_pair      = int(m.group(3))
                i_camera    = 0 if m.group(2) == 'left' else 1
                dot_spacing = float(m.group(6))
                Ndetected   = int(m.group(7))
                return path,i_frame,i_pair,i_camera,dot_spacing,Ndetected
            raise Exception("Couldn't parse image header line '{}'".format(l))





        # Keep going until I get a full frame's worth of data or until there's
        # nothing else to read
        while True:

            # Grab the next non-comment line
            while True:
                try:
                    l = next(f)
                except:
                    return None,None,None,None,None,None
                if l[0] != '#':
                    break

            path,i_frame,i_pair,i_camera,dot_spacing,Ndetected = parse_image_header(l)

            if Ndetected != Nwant*Nwant:
                if Ndetected != 0:
                    raise Exception("I can't handle incomplete board observations yet: {}".format(path))
                continue

            # OK then. I have dots to look at
            dots = np.zeros((Nwant,Nwant,2), dtype=float)

            for point_index in xrange(Ndetected):
                l = next(f)
                lf = l.split()
                if lf[0] != path:
                    raise Exception("Unexpected incomplete observation. Expected path '{}' but got '{}'".
                                    format(path, lf[0]))
                idot_x,idot_y     = [int(x)   for x in lf[6 :8 ]]
                dot2d_x, dot2d_y  = [float(x) for x in lf[10:12]]

                # I only accept complete observations of the cal board for now
                idot_x_want = int(point_index / 10)
                idot_y_want = point_index - idot_x_want*10

                if datafile_asciilog[0]=='v':
                    idot_x_want,idot_y_want = idot_y_want,idot_x_want

                if idot_x != idot_x_want or idot_y != idot_y_want:
                    raise Exception("Unexpected dot index. Line: '{}'".format(l))

                dots[idot_y,idot_x,:] = (dot2d_x,dot2d_y)

            return path,i_frame,i_pair,i_camera,dot_spacing,dots










    # dimensions (Nobservations, Nwant,Nwant, 2)
    dots                 = np.array(())
    # dimension (Nobservations, 2). Each row is (i_frame_consecutive, i_camera)
    indices_frame_camera = np.array((), dtype=np.int32)

    paths = []
    inputs = { 'imager_size':    (imager_w_estimate, imager_w_estimate),
                 'focal_estimate': (focal_estimate, focal_estimate),
                 'Nwant':          Nwant}

    i_frame_consecutive   = -1
    i_frame_last          = -1
    seen_cameras          = set()

    # I want the data to come in order:
    # frames - pairs - cameras - dots

    # Data. Axes: (idot_y, idot_x, idot2d_xy)
    # So the observed pixel coord of the dot (3,4) is
    # the 2-vector dots[3,4,:]
    dots        = np.array( (), dtype=float)
    dot_spacing = None

    point_index = 0

    with open(datafile, 'r') as f:

        l = next(f)
        if l != '# path fixture_size_m fixture_space_m fixture_cols fixture_rows num_dots_detected dot_fixture_col dot_fixture_row dot_fixture_physical_x dot_fixture_physical_y dot_image_col dot_image_row\n':
            raise Exception("Unexpected legend in '{}'. Got: '{}".format(datafile,l))

        while True:
            path,i_frame,i_pair,i_camera,dot_spacing,dots_here = get_next_dots(f)
            if i_frame is None:
                break

            if i_frame != i_frame_last:
                new_frame = True

            # make sure I get the ordering I want: frames - pairs - cameras
            if i_frame != i_frame_last:
                # if i_frame < i_frame_last:
                #     raise Exception("Non-consecutive i_frame: got {} and then {}".
                #                     format(i_frame_last, i_frame))
                # disabled because the seahunter data has different directories and non-consecutive frames will thus result

                i_frame_last         = i_frame
                i_pair_last          = i_pair
                i_camera_last        = i_camera
            elif i_pair != i_pair_last:
                if i_pair < i_pair_last:
                    raise Exception("Non-consecutive i_pair: got {} and then {}".
                                    format(i_pair_last, i_pair))
                i_pair_last          = i_pair
                i_camera_last        = i_camera
            elif i_camera != i_camera_last:
                if i_camera < i_camera_last:
                    raise Exception("Non-consecutive i_camera: got {} and then {}".
                                    format(i_camera_last, i_camera))
                i_camera_last        = i_camera

            if i_pair != pair_want:
                continue




            if new_frame:
                i_frame_consecutive += 1
                new_frame = False


            seen_cameras.add(i_camera)

            if not 'dot_spacing' in inputs:
                inputs['dot_spacing'] = dot_spacing
            else:
                if dot_spacing != inputs['dot_spacing']:
                    raise Exception("Inconsistent dot spacing")

            dots = nps.glue(dots, dots_here, axis = -4)
            indices_frame_camera = \
                nps.glue(indices_frame_camera,
                         np.array((i_frame_consecutive, i_camera), dtype=np.int32),
                         axis=-2)
            paths.append(path)



        if min(seen_cameras) != 0:
            raise Exception("Min camera index must be 0, but got {}".format(min(seen_cameras)))
        inputs['Ncameras'] = max(seen_cameras) + 1
        if inputs['Ncameras'] != len(seen_cameras):
            raise Exception("Non-consecutive cam indices: min: {} max: {} len: {}". \
                            format(min(seen_cameras),max(seen_cameras),len(seen_cameras)))
        inputs['indices_frame_camera'] = indices_frame_camera
        inputs['paths']                = paths
        inputs['dots']                 = dots
        return inputs
def read_dots():
    try:
        return _read_dots_stcal(datadir_stcal)
    except:
        return _read_dots_asciilog(datafile_asciilog)

def filter_inputs_for_camera(inputs, camera_want):
    r'''Returns a subset of the input, containing only the given camera

    Note that the resulting frames and cameras are renumbered to fill in
    gaps. The output is going to contain ONLY camera 0

    '''
    i = inputs['indices_frame_camera'][:,1] == camera_want

    out = {}
    out['focal_estimate'] = inputs['focal_estimate']
    out['dot_spacing']    = inputs['dot_spacing']
    out['Nwant']          = inputs['Nwant']
    out['imager_size']    = inputs['imager_size']
    out['Ncameras']       = 1
    out['paths']          = np.array(inputs['paths'])[i]
    out['dots']           = inputs['dots' ][i, ...]

    # each observation is a separate "frame"
    Nframes = out['paths'].shape[0]
    out['indices_frame_camera'] = \
        nps.transpose( nps.glue( np.arange(Nframes, dtype=np.int32),
                                 np.zeros (Nframes, dtype=np.int32),
                                 axis = -2) ).copy()
    return out

def split_inputs_by_camera(inputs):
    r'''Splits a single joint input into separate ones, for each camera'''

    Ncameras = inputs['Ncameras']

    return [filter_inputs_for_camera(inputs, camera) for camera in xrange(Ncameras)]


def join_inputs_and_solutions(separate_inputs, joint_intrinsics_frames, rt_cam1_fromref):
    r'''Joins two separate-camera inputs into a single one

    The output of this function is not going to be solvable: there're no
    observation that tie the cameras together in any way. But such measurements
    can be added to the output of this function

    '''
    Ncameras = len(separate_inputs)
    if Ncameras != 2:
        raise Exception("join_inputs_and_solutions() is stupid and can only join 2 cameras for now")

    out = {}
    out['focal_estimate'] = separate_inputs[0]['focal_estimate']
    out['dot_spacing']    = separate_inputs[0]['dot_spacing']
    out['Nwant']          = separate_inputs[0]['Nwant']
    out['imager_size']    = separate_inputs[0]['imager_size']
    out['Ncameras']       = Ncameras
    out['paths']          = nps.glue(*[separate_inputs[i]['paths'] for i in xrange(Ncameras)], axis=-1)
    out['dots']           = nps.glue(*[separate_inputs[i]['dots' ] for i in xrange(Ncameras)], axis=-4)

    intrinsics = nps.glue(*[joint_intrinsics_frames[i][0] for i in xrange(len(separate_inputs))], axis=-2)

    Nframes0 = separate_inputs[0]['indices_frame_camera'].shape[0]
    Nframes1 = separate_inputs[1]['indices_frame_camera'].shape[0]

    out['indices_frame_camera'] = \
        nps.glue( separate_inputs[0]['indices_frame_camera'],
                  separate_inputs[1]['indices_frame_camera'],
                  axis=-2)
    out['indices_frame_camera'][Nframes0:,0] += Nframes0
    out['indices_frame_camera'][Nframes0:,1] = 1

    # the frames for camera1 are given in respect to camera1, but they need to
    # be given in respect to cam0
    #   T0r = T01 T1r: R01 R1r x + R01 t1r + t01
    frames0 = joint_intrinsics_frames[0][1]
    frames1 = joint_intrinsics_frames[1][1]
    R10 = utils.Rodrigues_toR_broadcasted(rt_cam1_fromref[:3])
    t10 = rt_cam1_fromref[3:]
    t01 = -nps.matmult(t10, R10)
    R01 = nps.transpose(R10)
    R1r = utils.Rodrigues_toR_broadcasted(frames1[:, :3])     # shape: N,3,3
    t1r = nps.dummy(frames1[:, 3:], -2)                       # shape: N,1,3
    R0r = nps.matmult(R01, R1r)                               # shape: N,3,3
    t0r = (nps.matmult(t1r, R10) + t01)[:,0,:]                # shape: N,3
    r0r = utils.Rodrigues_tor_broadcasted(R0r)

    frames = nps.glue( frames0,
                       nps.glue(r0r, t0r, axis=-1),
                       axis=-2 )

    return intrinsics, nps.dummy(rt_cam1_fromref, -2), frames, out


cachefile_dots = 'mrcal.dots.pair{}.pickle'.format(pair_want)
if( read_cache_dots ):
    with open(cachefile_dots, 'r') as f:
        inputs = pickle.load(f)
else:
    inputs = read_dots()
    with open(cachefile_dots, 'w') as f:
        pickle.dump( inputs, f, protocol=2)

separate_inputs = split_inputs_by_camera(inputs)




if 0:
    joint_intrinsics_frames = [solve_monocular(separate_inputs[index], index) \
                               for index in xrange(len(separate_inputs))]
    with open('/tmp/joint.pickle', 'w') as f:
        pickle.dump( joint_intrinsics_frames, f, protocol=2)
else:
    with open('/tmp/joint.pickle', 'r') as f:
        joint_intrinsics_frames = pickle.load(f)



rt_cam1_fromref = np.array((0,0,0, -2.0, 0, 0), dtype=float)
intrinsics,extrinsics,frames,inputs = join_inputs_and_solutions(separate_inputs, joint_intrinsics_frames, rt_cam1_fromref)


# projected = \
#     utils.project_points(intrinsics, extrinsics, frames,
#                          inputs['dot_spacing'], Nwant)
# err = utils.compute_reproj_error(projected, inputs['dots'],
#                                  inputs['indices_frame_camera'], Nwant)
# norm2_err_perimage = nps.inner( nps.clump(err,n=-3),  nps.clump(err,n=-3))
# rms_err_perimage   = np.sqrt( norm2_err_perimage / (Nwant*Nwant) )

# I now have a joint optimization problem. Two cameras, with completely
# independent observations. I add a few joint observations.
#
# I'm looking at frame 12 pair 1 of stereo-2017-09-15-Fri-14-58-34
observations_point = \
    np.array(( # left, right images in the clouds
        (690,   1223, 30000.0), (678,   1203, 30000.0),
        (177,   1291, 30000.0), (184,   1279, 30000.0),
        (3769,  1060, 30000.0), (3759,   997, 30000.0),
        (2939,  1173, 30000.0), (2915,  1120, 30000.0),

        # left-right images on the fiducials.
        # Bow-right and then center-right
        ( 898,1787,  9.3), ( 94, 1771, 11.3),
        (2306,2437,  2.9), (248, 2436,  3.9)))

indices_point_camera_points = np.array(((0,0), (0,1),
                                        (1,0), (1,1),
                                        (2,0), (2,1),
                                        (3,0), (3,1),
                                        (4,0), (4,1),
                                        (5,0), (5,1)), dtype=np.int32)

# I seed all the points to be 20m ahead. Good enough
Npoints = indices_point_camera_points[-1,0] + 1
points = np.zeros((Npoints,3), dtype=float)
points[:,2] = 20

cachefile_solution = 'mrcal.solution.pair{}.pickle'.format(pair_want)
if False:
    mrcal.optimize(intrinsics, extrinsics, frames, points,
                   inputs['dots'], inputs['indices_frame_camera'],
                   observations_point, indices_point_camera_points,
                   "DISTORTION_CAHVOR", False, False,
                   None, None)

    mrcal.optimize(intrinsics, extrinsics, frames, points,
                   inputs['dots'], inputs['indices_frame_camera'],
                   observations_point, indices_point_camera_points,
                   "DISTORTION_CAHVOR", True, True,
                   None, None)
    with open(cachefile_solution, 'w') as f:
        pickle.dump( (intrinsics, extrinsics, frames, points, inputs), f, protocol=2)
else:
    with open(cachefile_solution, 'r') as f:
        (intrinsics, extrinsics, frames, points, inputs) = pickle.load(f)



# I have a calibration. We want non-identity intrinsics for some reason, so I do
# that
Rt_r0 = np.array([[ 0.,  0.,  1.],
                  [ 1.,  0.,  0.],
                  [ 0.,  1.,  0.],
                  [ 0.,  0.,  0.]])
Rr0 = Rt_r0[:3,:]
tr0 = Rt_r0[ 3,:]

# Tr1 = Tr0 T01 -> Rr0 R01 x + Rr0 t01 + tr0
R10 = utils.Rodrigues_toR_broadcasted(extrinsics[0,:3])
t10 = extrinsics[0,3:]
t01 = -nps.matmult(t10, R10)
Rr1 = nps.matmult(Rr0, nps.transpose(R10))
tr1 = nps.matmult(t01, nps.transpose(Rr0)) + tr0

Rt_r1 = nps.glue(Rr1, tr1, axis=-2)


dir_to = '/tmp'
c0 = camera_models.assemble_cahvor(intrinsics[0], Rt_r0)
c1 = camera_models.assemble_cahvor(intrinsics[1], Rt_r1)
camera_models.write_cahvor('{}/camera{}-{}.cahvor'.format(dir_to, pair_want, 0), c0)
camera_models.write_cahvor('{}/camera{}-{}.cahvor'.format(dir_to, pair_want, 1), c1)




# # and write out the resulting cahvor files
# cahvor = [ camera_models.assemble_cahvor( intrinsics[i], extrinsics[i-1] if i >= 1 else None ) \
#            for i in xrange(inputs['Ncameras']) ]

# # graft
# dir_from = '/tmp/seahuntercal_graft2'
# dir_to   = '/tmp/seahuntercal_graft3'
# cref = camera_models.parse_cahvor('{}/camera{}-{}.cahvor'.format(dir_from, pair_want, camera_want))
# eref = camera_models.get_extrinsics_Rt_toref(cref)
# c = camera_models.assemble_cahvor(intrinsics[0],eref)
# camera_models.write_cahvor('{}/camera{}-{}.cahvor'.format(dir_to, pair_want, camera_want),
#                            c)

# image_corrected = \
#     utils.undistort_image(intrinsics.ravel(),
#                           "/tmp/input-00012-{}-{}.jpg". \
#                           format(pair_want, "left" if camera_want == 0 else "right"))
# imagefile_corrected = "/tmp/input-00012-{}-{}_undistorted.jpg". \
#     format(pair_want, "left" if camera_want == 0 else "right")


# cv2.imwrite(imagefile_corrected, image_corrected)

# gp.plot( (image_corrected[1200:1500,:,0], {'with': 'image', 'tuplesize': 3}),
#          yinv=1 )
# import time
# time.sleep(2000)
# sys.exit()





# # camera_models.write_cahvor( "camera{}-0.cahvor".format(pair_want), cahvor[0] )
# # camera_models.write_cahvor( "camera{}-1.cahvor".format(pair_want), cahvor[1] )
