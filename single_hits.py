import config as cf
import data_containers as dc
import lar_param as lar
from rtree import index
import numpy as np
import channel_mapping as cmap
from itertools import chain
from collections import Counter

def t(p, q, r):
    x = p-q
    return np.dot(r-q, x)/np.dot(x, x)

def dist(p, q, r):
    return np.linalg.norm(t(p, q, r)*(p-q)+q-r)


def closest_activity(x0,y0,z0):
    d_min = 9999;
    for t in dc.tracks3D_list:
        start = np.array([t.ini_x, t.ini_y, t.ini_z])
        end = np.array([t.end_x, t.end_y, t.end_z])
        hit = np.array([x0, y0, z0])
        
        d = dist(start, end, hit)
        if(d < d_min):
            d_min = d

    return d_min

def in_veto_region(ch, t, hit_ch, hit_t, nchan_int, nticks_int):
    if(ch >= hit_ch - nchan_int and ch <= hit_ch + nchan_int):
        if(t >= hit_t - nticks_int and t <= hit_t +nticks_int):
            return False
    return True

def veto(hits, nchan, nticks, nchan_int, nticks_int):
    """ find the largest hit in the collection view """
    best_hit = -1
    max_Q = 0.
    for h in hits:
        if(cf.view_type[h.view] != 'Collection'):
            continue
        if(h.charge > max_Q):
            max_Q = h.charge
            best_hit = h


    if(best_hit.daq_channel+1 in cf.broken_channels):
        return True, -1

    if(best_hit.daq_channel-1 in cf.broken_channels):
        return True, -1

    """ time/position cut """
    if(best_hit.channel < nchan or best_hit.channel > cf.view_nchan[best_hit.view]-nchan):
        return True, -1

    if(best_hit.max_t < nticks or best_hit.max_t > cf.n_sample-nticks):
        return True, -1


    
    tmin, tmax = best_hit.max_t - nticks, best_hit.max_t + nticks+1
    vetoed = False
    
    for i in range(cf.n_tot_channels):
        module, view, chan = dc.chmap[i].get_ana_chan()

        if(i in cf.broken_channels):
            continue
            
        if(view == best_hit.view and chan >= best_hit.channel - nchan and chan <= best_hit.channel + nchan):

            roi = ~dc.mask_daq[i, tmin:tmax]
            index = np.where(roi==True)[0]
            
            for ir in index:
                vetoed = vetoed | in_veto_region(chan, ir+tmin, best_hit.channel, best_hit.max_t, nchan_int, nticks_int)


    if(vetoed == False):
        integrated_q = np.sum(dc.data[best_hit.module, best_hit.view, best_hit.channel-nchan_int:best_hit.channel+nchan_int+1, best_hit.start-nticks_int:best_hit.stop+nticks_int])
        integrated_q *= dc.chmap[best_hit.daq_channel].gain
        return False, integrated_q

    return True, -1
    
            




def compute_sh_properties(hits):
    charge_pos = 0.
    charge_neg = 0.
    min_t = 99999
    max_t = 0
    max_Q = 0.
    view = -1
    for h in hits:
        if(h.charge > max_Q):
            min_t = h.min_t
            max_t = h.max_t
            view = h.view

        charge_pos += h.charge_pos
        charge_neg += h.charge_neg
    

    return view, charge_pos, charge_neg, max_t, min_t

def check_nmatch(ov):
    nmatch = [len(x) for x in ov]
    is_good = True
    for x in nmatch:
        is_good = is_good and (x>0 and x <3)
    return is_good
    
def find_outliers(coord, d_max):
    med_x = np.median([x[2] for x in coord])
    med_y = np.median([x[3] for x in coord])
    
    dx = [np.fabs(med_x-x[2]) for x in coord]
    dy = [np.fabs(med_y-x[3]) for x in coord]
    out = []
    out.extend([(x[0],x[1]) for x,d in zip(coord,dx) if d>d_max])
    out.extend([(x[0],x[1]) for x,d in zip(coord,dy) if d>d_max])

    n_outliers = len(out)
    out = list(chain(*out))
    c = Counter(out)

    ID_to_rm = [k for k,v in c.items() if v==n_outliers]

    return med_x, med_y, ID_to_rm

def get_hit_xy(ha, hb):
    v_a, v_b = ha.view, hb.view
    ang_a = np.radians(cf.view_angle[v_a])
    ang_b = np.radians(cf.view_angle[v_b])
    x_a, x_b = ha.X, hb.X

    A = np.array([[np.cos(ang_a), -np.cos(ang_b)],
                  [-np.sin(ang_a), np.sin(ang_b)]])
    
    D = A[0,0]*A[1,1]-A[0,1]*A[1,0]

    """ this should never happen though """
    if(D == 0.):
        print("MEGA PBM :::  DETERMINANT IS ZERO")
        return -9999, -9999

    
    xy = A.dot([x_b, x_a])/D
    x, y = xy[0], xy[1]
    
    
    return x,y


def same_view_compatibility(ha, hb):
    if(np.fabs(ha.channel - hb.channel)!=1):
        return False
    if(np.fabs(ha.max_t-hb.max_t)>15):
        return False
    return True



def single_hit_finder():    
    cmap.arange_in_view_channels()

    time_tol = dc.reco['single_hit']['time_tol']
    outlier_dmax = dc.reco['single_hit']['outlier_dmax']
    veto_nchan = dc.reco['single_hit']['veto_nchan']
    veto_nticks = dc.reco['single_hit']['veto_nticks']
    int_nchan = dc.reco['single_hit']['int_nchan']
    int_nticks = dc.reco['single_hit']['int_nticks']



    if(len(dc.hits_list) < 3):
        return

    ID_shift = dc.hits_list[0].ID


    pties = index.Property()
    pties.dimension = 2

    ''' create an rtree index (3D : view, time)'''
    rtree_idx = index.Index(properties=pties)

    """ make a subset of unmatched hits """
    free_hits = [x for x in dc.hits_list if x.matched==-9999]


    for h in free_hits: 
        start = h.start
        stop  = h.stop

        rtree_idx.insert(h.ID, (h.view, start, h.view, stop))
        #i+=1
        #idx_to_ID.append(h.ID)

    """
    ID_to_idx = [-1]*(max(idx_to_ID)+1)

    for idx, ID in enumerate(idx_to_ID):
        ID_to_idx[ID] = idx
    """

    for h in free_hits:
        if(h.matched != -9999):
            continue
        start = h.start
        stop  = h.stop

        overlaps = [[] for x in range(cf.n_view)]

        for iview in range(cf.n_view):
            intersect = list(rtree_idx.intersection((iview, start, iview, stop)))
            [overlaps[iview].append(dc.hits_list[k-ID_shift]) for k in intersect]

        if(check_nmatch(overlaps)==False):
            continue


        
        coord = []
        for iview in range(cf.n_view-1):
            for ha in overlaps[iview]:
                for jview in range(iview+1, cf.n_view):
                    for hb in overlaps[jview]:
                        x,y = get_hit_xy(ha, hb)                        
                        coord.append((ha.ID, hb.ID, x, y))


        med_x, med_y, to_rm = find_outliers(coord, outlier_dmax)

        i=0
        while(i < len(to_rm)):
            o = to_rm[i]
            for iv in range(len(overlaps)):
                for ih in range(len(overlaps[iv])):
                    if(overlaps[iv][ih].ID == o):
                        overlaps[iv].pop(ih)
                        i+=1
                        break
            i+=1
        if(check_nmatch(overlaps)==False):
            continue


        med_z = np.median([x.Z for x in list(chain(*overlaps))])
        d_min = closest_activity(med_x, med_y, med_z)


        nhits = [len(x) for x in overlaps]

        IDs = [[x.ID for x in ov] for ov in overlaps]


        sh = dc.singleHits(nhits, IDs, med_x, med_y, med_z, d_min)
        
        v, q = veto(overlaps[2], veto_nchan, veto_nticks, int_nchan, int_nticks)

        sh.set_veto(v,q)
        
        for ov in overlaps:            
            sh.set_view(*compute_sh_properties(ov))
            
            for hit in ov:
                hit.set_match(-5555)
                start, stop = hit.start, hit.stop
                rtree_idx.delete(hit.ID, (hit.view, start, hit.view, stop))

        dc.single_hits_list.append(sh)
        dc.evt_list[-1].n_single_hits += 1
