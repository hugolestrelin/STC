#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Opens a FLEXPART type grib file from ECMWF and reads meteorological fields.
Requires pygrib, loaded from conda-forge
For both python 2 and python 3 under anaconda
ACHTUNG: in some installations of python 3, error at loading stage because
of libZ not found. Remedy: load netCDF4 in the calling program even if not used.

Functions: Open files, read the data, extract subgrids, make charts, interpolate in time, 
interpolate to pressure levels

Data are read on hybrid levels. They can interpolated to pressure levels. Do it on subgrids
as it is a time consuming procedure.

Usage:
>> from ECMWF_N import ECMWF
open files for a date (datetime) and a project (VOLC or STC)
>> data = ECMWF(project,date)
read a variable var
>> data._get_var(var)
example: data._get_var('CC') for cloud cover (see lists in ECMWF class)
calculate pressure field
>> data._mkp()
calculate potential temperature (requires to read T and calculate P before)
>> data._mktheta()
extract a subgrid
>> data1 = data.extract(latRange=[lat1,lat2],lonRange=[lon1,lon2])
plot a chart for the variable var and level lev (lev is the level number)
>> data.chart(var,lev)
interpolate in time (date2 must be between the dates of data0 and data1)
>> data2 = data0.interpol-time(data1,date2)
interpolate to pressure levels
>> data1 = data.interpolP(pList,varList,latRange,lonRange)
where pList is a pressure or a list of pressures, varList is a variable or a list of variables, 
latRange and lonRange as in extract
    
The ECMWF class is used to read the file corresponding to a date for several projects/
The relevant projects are STC and VOLC
  
The ECMWF_pure allows to define a template object without reading files.
It can be used to modify data. It is produced as an output of the interpolation.

Created on 21/01/2018 from ECMWF.py

@author: Bernard Legras (legras@lmd.ens.fr)
@licence: CeCILL-C
"""
from __future__ import division, print_function
#from __future__ import unicode_literals
from datetime import datetime
import numpy as np
import pygrib
import os
from mpl_toolkits.basemap import Basemap
import matplotlib.pyplot as plt
import socket
from scipy.interpolate import PchipInterpolator, RegularGridInterpolator

MISSING = -999
# Physical constants
R = 287.04 # or 287.053
Cpd = 1005.7
kappa = R/Cpd
g = 9.80665
pref = 101325.
p0 = 100000.

def strictly_increasing(L):
    return all(x<y for x, y in zip(L, L[1:]))

# template object produced by extraction and interpolation
class ECMWF_pure(object):
    def __init__(self):
        self.var={}
        self.attr={}
        self.warning = []

    def chart(self,var,lev=0,txt='',log=False):
        # test existence of key field
        if var not in self.var.keys():
            print ('undefined field')
            return

        fig=plt.figure(figsize=[11,4])
        m = Basemap(projection='cyl', llcrnrlat=self.attr['lats'][0],
                    urcrnrlat=self.attr['lats'][-1],
                    llcrnrlon=self.attr['lons'][0],
                    urcrnrlon=self.attr['lons'][-1], resolution='c')
        m.drawcoastlines(color='k')
        m.drawcountries(color='k')
        if self.attr['lons'][-1] - self.attr['lons'][0] <= 50.:
            spacex = 5.
        else:
            spacex = 10.
        if self.attr['lats'][-1] - self.attr['lats'][0] <= 50.:
            spacey = 5.
        else:
            spacey = 10.
        meridians = np.arange(self.attr['lons'][0], self.attr['lons'][-1], spacex)
        parallels = np.arange(self.attr['lats'][0], self.attr['lats'][-1], spacey)
        m.drawmeridians(meridians, labels=[0, 0, 0, 1], fontsize=8)
        m.drawparallels(parallels, labels=[1, 0, 0, 0], fontsize=8)        
        if len(self.var[var].shape) == 3:
            buf = self.var[var][lev,:,:]
        else:
            buf = self.var['var']
        if log:
            buf = np.ma.masked_less_equal(buf,0)
            buf = np.log(buf)                   
        iax = plt.imshow(buf, interpolation='nearest',
                     extent=[self.attr['lons'][0], self.attr['lons'][-1],
                             self.attr['lats'][0], self.attr['lats'][-1]],
                     origin='lower', aspect=1.,cmap='jet')
        cax = fig.add_axes([0.91, 0.15, 0.03, 0.7])
        fig.colorbar(iax, cax=cax)
        plt.title(txt)
        plt.show()
        return None

    def extract(self,latRange=None,lonRange=None):
        """ extract all variables on a reduced grid """
        # first determine the boundaries of the domain
        new = ECMWF_pure()
        if (latRange == []) | (latRange == None):
            new.attr['lats'] = self.attr['lats']
            nlatmin = 0
            nlatmax = len(self.attr['lats'])
        else:
            nlatmin = np.abs(self.attr['lats']-latRange[0]).argmin()
            nlatmax = np.abs(self.attr['lats']-latRange[1]).argmin()+1
        if (lonRange == []) | (lonRange == None):
            new.attr['lons'] = self.attr['lons']
            nlonmin = 0
            nlonmax = len(self.attr['lons'])
        else:
            nlonmin = np.abs(self.attr['lons']-lonRange[0]).argmin()
            nlonmax = np.abs(self.attr['lons']-lonRange[1]).argmin()+1
        new.attr['lats'] = self.attr['lats'][nlatmin:nlatmax]
        new.attr['lons'] = self.attr['lons'][nlonmin:nlonmax]
        new.nlat = len(new.attr['lats'])
        new.nlon = len(new.attr['lons'])
        new.nlev = self.nlev
        new.attr['levtype'] = self.attr['levtype']
        new.date = self.date
        # extraction
        for var in self.var.keys():
            if len(self.var[var].shape) == 3:
                new.var[var] = self.var[var][:,nlatmin:nlatmax,nlonmin:nlonmax]
            else:
                new.var[var] = self.var[var][nlatmin:nlatmax,nlonmin:nlonmax]
        return new
    
    def getxy(self,var,lev,y,x):
        """ get the interpolated value of var in x, y on the level lev """
        # Quick n' Dirty version by nearest neighbour
        jy = np.abs(self.attr['lats']-y).argmin()
        ix = np.abs(self.attr['lons']-x).argmin()
        return self.var[var][lev,jy,ix]       

    def interpol_time(self,other,date):
        # This code interpolate in time between two ECMWF objects with same vars
        # check the date
        if self.date < other.date:
            if (date > other.date) | (date < self.date):
                print ('error on date')
                print (self.date,date,other.date)
                return -1
        else:
            if (date < other.date) | (date > self.date):
                print ('error on date')
                print (other.date,date,self.date)
                return -1
        # calculate coefficients
        dt = (other.date-self.date).total_seconds()
        dt1 = (date-self.date).total_seconds()
        dt2 = (other.date-date).total_seconds()
        cf1 = dt2/dt
        cf2 = dt1/dt
        #print ('cf ',cf1,cf2)
        data = ECMWF_pure()
        data.date = date
        for names in ['lons','lats']:
            data.attr[names] = self.attr[names]
        data.nlon = self.nlon
        data.nlat = self.nlat
        data.nlev = self.nlev
        for var in self.var.keys():
            data.var[var] = cf1*self.var[var] + cf2*other.var[var]
        return data
    
    def interpolP(self,p,varList='All',latRange=None,lonRange=None):
        """ interpolate the variables to a pressure level or a set of pressure levels
            vars must be a list of variables or a single varibale
            p must be a list of pressures in Pascal
        """
        if 'P' not in self.var.keys():
            self._mkp()
        new = ECMWF_pure()
        if varList == 'All':
            varList = list(self.var.keys())
            varList.remove('SP')
            varList.remove('P')
        elif type(varList) == str:
            varList = [varList,]
        for var in varList:
            if var not in self.var.keys():
                print(var,' not defined')
                return
        if type(p) != list:
            p = [p,]
        if 'P' not in self.var.keys():
            print('P not defined')
            return
        # first determine the boundaries of the domain
        if (latRange == []) | (latRange == None):
            nlatmin = 0
            nlatmax = self.nlat
        else:
            nlatmin = np.abs(self.attr['lats']-latRange[0]).argmin()
            nlatmax = np.abs(self.attr['lats']-latRange[1]).argmin()+1
        if (lonRange == []) | (lonRange == None):
            nlonmin = 0
            nlonmax = self.nlon
        else:
            nlonmin = np.abs(self.attr['lons']-lonRange[0]).argmin()
            nlonmax = np.abs(self.attr['lons']-lonRange[1]).argmin()+1
        new.attr['lats'] = self.attr['lats'][nlatmin:nlatmax]
        new.attr['lons'] = self.attr['lons'][nlonmin:nlonmax]
        new.nlat = len(new.attr['lats'])
        new.nlon = len(new.attr['lons'])
        new.date = self.date
        new.nlev = len(p)
        new.attr['levtype'] = 'pressure'
        new.attr['plev'] = p
        pmin = np.min(p)
        pmax = np.max(p)
        for var in varList:
            new.var[var] = np.empty(shape=(len(p),nlatmax-nlatmin,nlonmax-nlonmin))
            jyt = 0
            # big loop that should be paralellized or calling numa for good performance
            for jys in range(nlatmin,nlatmax):
                ixt = 0
                for ixs in range(nlonmin,nlonmax):
                    # find the range of p in the column
                    npmin = np.abs(self.var['P'][:,jys,ixs]-pmin).argmin()
                    npmax = np.abs(self.var['P'][:,jys,ixs]-pmax).argmin()+1
                    npmin = max(npmin - 3,0)
                    npmax = min(npmax + 3,self.nlev)
                    fint = PchipInterpolator(np.log(self.var['P'][npmin:npmax,jys,ixs]),
                                         self.var[var][npmin:npmax,jys,ixs])
                    new.var[var][:,jyt,ixt] = fint(np.log(p))
                    ixt += 1
                jyt += 1
        return new


# standard class to read data
class ECMWF(ECMWF_pure):
    # to do: raise exception in case of an error
    
    def __init__(self,project,date):
        ECMWF_pure.__init__(self)
        self.project = project
        self.date = date
        if self.project=='VOLC':
            if 'grapelli' in socket.gethostname():
                self.rootdir = '/dsk2/ERA5/VOLC'
            elif 'ens' in socket.gethostname():
                self.rootdir = '/net/grapelli/dsk2/ERA5/VOLC'
            else:
                print('unknown hostname for this dataset')
                return
            SP_expected = False
            EN_expected = True
            DI_expected = True
            WT_expected = True
            VD_expected = False
        elif project=='STC':
            if 'gort' == socket.gethostname():
                self.rootdir = '/dkol/dc6/samba/STC/STC'
            elif 'ciclad' in socket.gethostname():
                self.rootdir = '/data/legras/flexpart_in/STC/ERA5'
            else:
                print('unknown hostname for this dataset')
                return
            SP_expected = False
            EN_expected = True
            DI_expected = True
            WT_expected = True
            VD_expected = False
        else:
            print('Non implemented project')
            return

        if SP_expected:
            self.fname = 'SP'+date.strftime('%y%m%d%H')
            path1 = 'SP-true/grib'
        if EN_expected:
            self.fname = 'EN'+date.strftime('%y%m%d%H')
            path1 = 'EN-true/grib'
            self.ENvar = {'U':['u','U component of wind','m s**-1'],
                     'V':['v','V component of wind','m s**-1'],
                     'W':['w','Vertical velocity','Pa s**-1'],
                     'T':['t','Temperature','K'],
                     'LNSP':['lnsp','Logarithm of surface pressure','Log(Pa)']}
            self.DIvar = {'ASSWR':['mttswr','Mean temperature tendency due to short-wave radiation','K s**-1'],
                     'ASLWR':['mttlwr','Mean temperature tendency due to long-wave radiation','K s**-1'],
                     'CSSWR':['mttswrcs','Mean temperature tendency due to short-wave radiation, clear sky','K s**-1'],
                     'CSLWR':['mttlwrcs','Mean temperature tendency due to long-wave radiation, clear sky','K s**-1'],
                     'PHR':['mttpm','Mean temperature tendency due to parametrisations','K s**-1'],
                     'UMF':['mumf','Mean updraught mass flux','kg m**-2 s**-1'],
                     'DMF':['mdmf','Mean downdraught mass flux','kg m**-2 s**-1'],
                     'UDR':['mudr','Mean updraught detrainment rate','kg m**-3 s**-1'],
                     'DDR':['mddr','Mean downdraught detrainement rate','kg m**-3 s**-1']}
            self.WTvar = {'CRWC':['crwc','Specific rain water content','kg kg**-1'],
                     'CSWC':['cswc','Specific snow water content','kg kg**-1'],
                     'Q':['q','Specific humidity','kg kg**-1'],
                     'QL':['clwc','Specific cloud liquid water content','kg kg**-1'],
                     'QI':['ciwc','Specific cloud ice water content','kg kg**-1'],
                     'CC':['cc','Fraction of cloud cover','0-1']}
            self.VDvar = {'DIV':['xxx','Divergence','s**-1'],
                          'VOR':['xxx','Vorticity','s**-1'],
                          'Z':['xxx','Geopotential','m'],
                          'WE':['xxx','Eta dot','s**-1*']}
        if DI_expected:
            self.dname = 'DI'+date.strftime('%y%m%d%H')
        if WT_expected:
            self.wname = 'WT'+date.strftime('%y%m%d%H')
        if VD_expected:
            self.wname = 'VD'+date.strftime('%y%m%d%H')
            
        # opening the files    
        try:
            self.grb = pygrib.open(os.path.join(self.rootdir,path1,date.strftime('%Y/%m'),self.fname))
        except:
            print('cannot open '+os.path.join(self.rootdir,path1,date.strftime('%Y/%m'),self.fname))
            return
        try:
            sp = self.grb.select(name='Surface pressure')[0]
            logp = False
        except:
            try:
                sp = self.grb.select(name='Logarithm of surface pressure')[0]
                logp = True
            except:
                print('no surface pressure in '+self.fname)
                self.grb.close()
                return
        # Check time matching  (made from validity date and time)
        vd = sp['validityDate']
        vt = sp['validityTime']
        day = vd % 100
        vd //=100
        month = vd % 100
        vd //=100
        minute = vt % 100
        vt //=100
        dateread = datetime(year=vd,month=month,day=day,hour=vt,minute=minute)
        if dateread != self.date:
            print('WARNING: dates do not match')
            print('called date    '+self.date.strftime('%Y-%m-%d %H:%M'))
            print('date from file '+dateread.strftime('%Y-%m-%d %H:%M'))
            # self.grb.close()
        # Get general info from this message
        self.attr['Date'] = sp['dataDate']
        self.attr['Time'] = sp['dataTime']
        self.attr['valDate'] = sp['validityDate']
        self.attr['valTime'] = sp['validityTime']
        self.nlon = sp['Ni']
        self.nlat = sp['Nj']
        self.attr['lons'] = sp['distinctLongitudes']
        self.attr['lats'] = sp['distinctLatitudes']
        self.attr['Lo1'] = sp['longitudeOfFirstGridPoint']/1000  # in degree
        self.attr['Lo2'] = sp['longitudeOfLastGridPoint']/1000  # in degree
        self.attr['La1'] = sp['latitudeOfFirstGridPoint']/1000  # in degree
        self.attr['La2'] = sp['latitudeOfLastGridPoint']/1000 # in degree
        if sp['PVPresent']==1 :
            pv = sp['pv']
            self.attr['ai'] = pv[0:int(pv.size/2)]
            self.attr['bi'] = pv[int(pv.size/2):]
            self.attr['am'] = (self.attr['ai'][1:] + self.attr['ai'][0:-1])/2
            self.attr['bm'] = (self.attr['bi'][1:] + self.attr['bi'][0:-1])/2
        else:
            # todo: provide a fix, eg for JRA-55
            print('missing PV not implemented')
        self.attr['levtype'] = 'hybrid'
        # Read the surface pressure
        if logp:
            self.var['SP'] = np.exp(sp['values'])
        else:
            self.var['SP'] = sp['values']
        #  Reverting lat order
        self.attr['lats'] = self.attr['lats'][::-1]
        self.var['SP']   = self.var['SP'][::-1,:]

        # Opening of the other files
        self.DI_open = False
        self.WT_open = False
        self.VD_open = False
        if DI_expected:
            try:
                self.drb = pygrib.open(os.path.join(self.rootdir,date.strftime('DI-true/grib/%Y/%m'),self.dname))
                self.DI_open = True
            except:
                print('cannot open '+os.path.join(self.rootdir,date.strftime('DI-true/grib/%Y/%m'),self.dname))
        if WT_expected:
            try:
                self.wrb = pygrib.open(os.path.join(self.rootdir,date.strftime('WT-true/grib/%Y/%m'),self.wname))
                self.WT_open = True
            except:
                print('cannot open '+os.path.join(self.rootdir,date.strftime('WT-true/grib/%Y/%m'),self.wname))
        if VD_expected:
            try:
                self.vrb = pygrib.open(os.path.join(self.rootdir,date.strftime('VD-true/grib/%Y/%m'),self.wname))
                self.VD_open = True
            except:
                print('cannot open '+os.path.join(self.rootdir,date.strftime('VD-true/grib/%Y/%m'),self.wname))

    def close(self):
        self.grb.close()
        try:
            self.drb.close()
        except:
            pass
        try:
            self.wrb.close()
        except:
            pass

# short cut for some common variables
    def _get_T(self):
        self._get_var('T')
    def _get_U(self):
        self._get_var('U')
    def _get_V(self):
        self._get_var('V')
    def _get_W(self):
        self._get_var('W')
    def _get_Q(self):
        self._get_var('Q')
        
# get a variable from the archive
    def _get_var(self,var):
        if var in self.var.keys():
            return
        try:
            if var in self.ENvar.keys():
                TT = self.grb.select(shortName=self.ENvar[var][0])
            elif var in self.DIvar.keys() and self.DI_open:
                TT = self.drb.select(shortName=self.DIvar[var][0])
            elif var in self.WTvar.keys() and self.WT_open:
                TT = self.wrb.select(shortName=self.WTvar[var][0])
            elif var in self.VDvar.keys() and self.VD_open:
                TT = self.vrb.select(shortName=self.VDvar[var][0])
            elif var in self.DIvar.keys() and not self.DI_open:
                print('DI file is not open for '+var)
            elif var in self.WTvar.keys() and not self.WT_open:
                print('WT file is not open for '+var)
            elif var in self.VDvar.keys() and not self.VD_open:
                print('VD file is not open for '+var)
            else:
                print(var+' not found')
        except:
            print(var+' not found or read error')
            return
        # Process each message corresponding to a level
        if 'levs' not in self.attr.keys():
            readlev=True
            self.nlev = len(TT)
            self.attr['levs'] = np.full(len(TT),MISSING,dtype=int)
            self.attr['plev'] = np.full(len(TT),MISSING,dtype=int)
        else:
            readlev=False
            if len(TT) !=  self.nlev:
                print('new record inconsistent with previous ones')
                return
        self.var[var] = np.empty(shape=[self.nlev,self.nlat,self.nlon])
        #print(np.isfortran(self.var[var]))

        for i in range(len(TT)):
            self.var[var][i,:,:] = TT[i]['values']
            if readlev:
                try:
                    lev = TT[i]['lev']
                except:
                    pass
                try:
                    lev = TT[i]['level']
                except:
                    pass
                self.attr['levs'][i] = lev
                self.attr['plev'][i] = self.attr['am'][lev-1] + self.attr['bm'][lev-1]*pref

#       # Check the vertical ordering of the file
        if readlev:
            if not strictly_increasing(self.attr['levs']):
                self.warning.append('NOT STRICTLY INCREASING LEVELS')
        # Reverting North -> South to South to North
        #print(np.isfortran(self.var[var]))
        self.var[var] = self.var[var][:,::-1,:]
        #print(np.isfortran(self.var[var]))
        return None
    
    def get_var(self,var):
        self._get_var(var)
        return self.var['var' ]

    def _mkp(self):
        # Calculate the pressure field
        self.var['P'] =  np.empty(shape=(self.nlev,self.nlat,self.nlon))
        for i in range(self.nlev):
            lev = self.attr['levs'][i]
            self.var['P'][i,:,:] = self.attr['am'][lev-1] \
                                 + self.attr['bm'][lev-1]*self.var['SP']

    def _mkpz(self):
        # Calculate pressure field for w (check)
        self.var['PZ'] =  np.empty(shape=(self.nlev,self.nlat,self.nlon))
        for i in range(self.nlev):
            lev = self.attr['levs'][i]
            self.var['PZ'][i,:,:] = self.attr['ai'][lev-1] \
                                  + self.attr['bi'][lev-1]*self.var['SP']

    def _mkthet(self):
        # Calculate the potential temperature
        if not set(['T','P']).issubset(self.var.keys()):
            print('T or P undefined')
            return
        self.var['PT'] = self.var['T'] * (p0/self.var['P'])**kappa

    def _mkrho(self):
        # Calculate the dry density
        if not set(['T','P']).issubset(self.var.keys()):
            print('T or P undefined')
            return
        self.var['RHO'] = (1/R) * self.var['P'] / self.var['T']

    def _mkrhoq(self):
        # Calculate the dry density
        if not set(['T','P','Q']).issubset(self.var.keys()):
            print('T, P or Q undefined')
            return
        pcor = 230.617*self.var['Q']*np.exp(17.5043*self.var['Q']/(241.2+self.var['Q']))
        self.var['RHO'] = (1/R) * (self.var['P'] - pcor) / self.var['T']

    def _checkThetProfile(self):
        # Check that the potential temperature is always increasing with height
        if 'PT' not in self.var.keys():
            print('first calculate PT')
            return
        ddd = self.var['PT'][:-1,:,:]-self.var['PT'][1:,:,:]
        for lev in range(ddd.shape[0]):
            if np.min(ddd[lev,:,:]) < 0:
                print('min level of inversion: ',lev)
                return lev

if __name__ == '__main__':
    date = datetime(2017,8,11,12)
    dat = ECMWF('STC',date)
    dat._get_T()
    dat._get_U()
    dat._get_var('ASLWR')
    dat._get_var('CC')
    dat._mkp()
    dat._mkthet()
    dat._checkThetProfile()