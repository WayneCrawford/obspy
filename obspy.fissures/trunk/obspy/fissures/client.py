#!/usr/bin/env python
#-------------------------------------------------------------------
# Filename: client.py
#  Purpose: Python client for the Data Handling Interface (DHI/Fissures)
#   Author: Moritz Beyreuther, Robert Barsch
#    Email: moritz.beyreuther@geophysik.uni-muenchen.de
#
# Copyright (C) 2008-2010 Moritz Beyreuther, Robert Barsch
#---------------------------------------------------------------------
"""
Data Handling Interface (DHI)/Fissures client.

Python function for accessing data from DHI/Fissures.
The method is based on omniORB CORBA requests.

:copyright: The ObsPy Development Team (devs@obspy.org)
:license: GNU Lesser General Public License, Version 3 (LGPLv3)
"""

from omniORB import CORBA
from CosNaming import NameComponent, NamingContext
from idl import Fissures
from obspy.core import Trace, UTCDateTime, Stream, AttribDict
from obspy.mseed.libmseed import LibMSEED
from obspy.fissures.util import FissuresException, FissuresWarning, \
        poleZeroFilter2PAZ, utcdatetime2Fissures, use_first_and_raise_or_warn
import math
import numpy as np
import sys
import warnings
from copy import deepcopy


class Client(object):
    """
    DHI/Fissures client class. For more informations see the
    :func:`~obspy.fissures.client.Client.__init__`
    method and all public methods of the client class.

    The Data Handling Interface (DHI) is a CORBA data access framework
    allowing users to access seismic data and metadata from IRIS DMC
    and other participating institutions directly from a DHI-supporting
    client program. The effect is to eliminate the extra steps of
    running separate query interfaces and downloading of data before
    visualization and processing can occur. The information is loaded
    directly into the application for immediate use.
    http://www.iris.edu/dhi/

    Detailed information on network_dc, seismogram_dc servers and CORBA:

    * http://www.seis.sc.edu/wily
    * http://www.iris.edu/dhi/servers.htm
    * http://www.seis.sc.edu/software/fissuresImpl/objectLocation.html

    Check availability of stations via SeismiQuery:

    * http://www.iris.edu/SeismiQuery/timeseries.htm

    .. note::
        Ports 6371 and 17508 must be open (IRIS Data and Name Services).
    """
    #
    # We recommend the port ranges 6371-6382, 17505-17508 to be open (this
    # is how it is configured in our institute).
    #
    def __init__(self, network_dc=("/edu/iris/dmc", "IRIS_NetworkDC"),
                 seismogram_dc=("/edu/iris/dmc", "IRIS_DataCenter"),
                 name_service="dmc.iris.washington.edu:6371/NameService",
                 debug=False):
        """
        Initialize Fissures/DHI client. 
        
        :param network_dc: Tuple containing dns and NetworkDC name.
        :param seismogram_dc: Tuple containing dns and DataCenter name.
        :param name_service: String containing the name service.
        :param debug:  Enables verbose output of the connection handling
                (default is False).
        """
        # Some object wide variables
        if sys.byteorder == 'little':
            self.byteorder = True
        else:
            self.byteorder = False
        #
        self.mseed = LibMSEED()
        #
        # Initialize CORBA object, see pdf in obspy.fissures/trunk/doc or
        # http://omniorb.sourceforge.net/omnipy3/omniORBpy/omniORBpy004.html
        # for available options
        args = ["-ORBgiopMaxMsgSize", "2097152",
                "-ORBInitRef",
                "NameService=corbaloc:iiop:" + name_service]
        if debug:
            args = ["-ORBtraceLevel", "40"] + args
        orb = CORBA.ORB_init(args, CORBA.ORB_ID)
        self.obj = orb.resolve_initial_references("NameService")
        #
        # Resolve naming service
        try:
            self.rootContext = self.obj._narrow(NamingContext)
        except:
            msg = "Could not connect to " + name_service
            raise FissuresException(msg)
        #
        # network and seismogram cosnaming
        self.net_name = self._composeName(network_dc, 'NetworkDC')
        self.seis_name = self._composeName(seismogram_dc, 'DataCenter')
        # resolve network finder
        try:
            netDC = self.rootContext.resolve(self.net_name)
            netDC = netDC._narrow(Fissures.IfNetwork.NetworkDC)
            netFind = netDC._get_a_finder()
            self.netFind = netFind._narrow(Fissures.IfNetwork.NetworkFinder)
        except:
            msg = "Initialization of NetworkFinder failed."
            warnings.warn(msg, FissuresWarning)
        # resolve seismogram DataCenter
        try:
            seisDC = self.rootContext.resolve(self.seis_name)
            self.seisDC = seisDC._narrow(Fissures.IfSeismogramDC.DataCenter)
        except:
            msg = "Initialization of seismogram DataCenter failed."
            warnings.warn(msg, FissuresWarning)
        # if both failed, client instance is useless, so raise
        if not self.netFind and not self.seisDC:
            msg = "Neither NetworkFinder nor DataCenter could be initialized."
            raise FissuresException(msg)


    def getWaveform(self, network_id, station_id, location_id, channel_id,
            start_datetime, end_datetime, getPAZ=False, getCoordinates=False):
        """
        Get Waveform in an ObsPy stream object from Fissures / DHI.

        >>> from obspy.core import UTCDateTime
        >>> from obspy.fissures import Client
        >>> client = Client()
        >>> t = UTCDateTime(2003,06,20,06,00,00)
        >>> st = client.getWaveform("GE", "APE", "", "SHZ", t, t+600)
        >>> print st
        1 Trace(s) in Stream:
        GE.APE..SHZ | 2003-06-20T06:00:00.001000Z - 2003-06-20T06:10:00.001000Z | 50.0 Hz, 30001 samples
        >>> st = client.getWaveform("GE", "APE", "", "SH*", t, t+600)
        >>> print st
        3 Trace(s) in Stream:
        GE.APE..SHZ | 2003-06-20T06:00:00.001000Z - 2003-06-20T06:10:00.001000Z | 50.0 Hz, 30001 samples
        GE.APE..SHN | 2003-06-20T06:00:00.001000Z - 2003-06-20T06:10:00.001000Z | 50.0 Hz, 30001 samples
        GE.APE..SHE | 2003-06-20T06:00:00.001000Z - 2003-06-20T06:10:00.001000Z | 50.0 Hz, 30001 samples

        :param network_id: Network id, 2 char; e.g. "GE"
        :param station_id: Station id, 5 char; e.g. "APE"
        :param location_id: Location id, 2 char; e.g. "  "
        :type channel_id: String, 3 char
        :param channel_id: Channel id, e.g. "SHZ". "*" as third letter is
                supported and requests "Z", "N", "E" components.
        :param start_datetime: UTCDateTime object of starttime
        :param end_datetime: UTCDateTime object of endtime
        :type getPAZ: Boolean
        :param getPAZ: Fetch PAZ information and append to
            :class:`~obspy.core.trace.Stats` of all fetched traces. This
            considerably slows down the request.
        :type getCoordinates: Boolean
        :param getCoordinates: Fetch coordinate information and append to
            :class:`~obspy.core.trace.Stats` of all fetched traces. This
            considerably slows down the request.
        :return: Stream object
        """
        # NOTHING goes ABOVE this line!
        # append all args to kwargs, thus having everything in one dictionary
        # no **kwargs in method definition, so we need a get with default here
        kwargs = locals().get('kwargs', {})
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value

        # intercept 3 letter channels with component wildcard
        # recursive call, quick&dirty and slow, but OK for the moment
        if len(channel_id) == 3 and channel_id.find("*") == 2:
            st = Stream()
            for cha in (channel_id[:2] + comp for comp in ["Z", "N", "E"]):
                # replace channel_id XXX a bit ugly:
                if 'channel_id' in kwargs:
                    kwargs.pop('channel_id')
                st += self.getWaveform(channel_id=cha, **kwargs)
            return st

        # get channel object
        channels = self._getChannelObj(network_id, station_id, location_id,
                channel_id)
        # get seismogram object
        seis = self._getSeisObj(channels, start_datetime, end_datetime)
        #
        # build up ObsPy stream object
        st = Stream()
        for sei in seis:
            # remove keep alive blockettes R
            if sei.num_points == 0:
                continue
            tr = Trace()
            tr.stats.starttime = UTCDateTime(sei.begin_time.date_time)
            tr.stats.npts = sei.num_points
            # calculate sampling rate
            unit = str(sei.sampling_info.interval.the_units.the_unit_base)
            if unit != 'SECOND':
                raise FissuresException("Wrong unit!")
            value = sei.sampling_info.interval.value
            power = sei.sampling_info.interval.the_units.power
            multi_factor = sei.sampling_info.interval.the_units.multi_factor
            exponent = sei.sampling_info.interval.the_units.exponent
            # sampling rate is given in Hertz within ObsPy!
            delta = pow(value * pow(10, power) * multi_factor, exponent)
            sr = sei.num_points / float(delta)
            tr.stats.sampling_rate = sr
            # set all kind of stats
            tr.stats.station = sei.channel_id.station_code
            tr.stats.network = sei.channel_id.network_id.network_code
            tr.stats.channel = sei.channel_id.channel_code
            tr.stats.location = sei.channel_id.site_code.strip()
            # loop over data chunks
            data = []
            for chunk in sei.data.encoded_values:
                # swap byte order in decompression routine if necessary 
                # src/IfTimeSeries.idl:52: FALSE = big endian format -
                swapflag = (self.byteorder != chunk.byte_order)
                compression = chunk.compression
                # src/IfTimeSeries.idl:44: const EncodingFormat STEIM2=11;
                if compression == 11:
                    data.append(self.mseed.unpack_steim2(chunk.values,
                                                         chunk.num_points,
                                                         swapflag=swapflag))
                # src/IfTimeSeries.idl:43: const EncodingFormat STEIM1=10;
                elif compression == 10:
                    data.append(self.mseed.unpack_steim1(chunk.values,
                                                         chunk.num_points,
                                                         swapflag=swapflag))
                else:
                    msg = "Compression %d not implemented" % compression
                    raise NotImplementedError(msg)
            # merge data chunks
            tr.data = np.concatenate(data)
            tr.verify()
            st.append(tr)
            # XXX: merging?
        st.trim(start_datetime, end_datetime)
        if getPAZ:
            # XXX channel_id ignored at the moment!!!! XXX
            if "*" in channel_id:
                if len(channel_id) < 3:
                    msg = "Cannot fetch PAZ with wildcarded band codes."
                    raise FissuresException(msg)
                channel_id = channel_id.replace("*", "Z")
                msg = "Wildcard in channel_id, trying to look up Z " + \
                      "components PAZ information"
                warnings.warn(msg, FissuresWarning)
            # XXX should add a check like metadata_check in seishub.client
            data = self.getPAZ(network_id=network_id, station_id=station_id,
                               datetime=start_datetime)
            for tr in st:
                tr.stats['paz'] = deepcopy(data)
        if getCoordinates:
            # XXX should add a check like metadata_check in seishub.client
            data = self.getCoordinates(network_id=network_id,
                                       station_id=station_id,
                                       datetime=start_datetime)
            for tr in st:
                tr.stats['coordinates'] = deepcopy(data)
        return st


    def getNetworkIds(self):
        """
        Return all available network_ids as list.

        :note: This takes a very long time.
        """
        # Retrieve all available networks
        net_list = []
        networks = self.netFind.retrieve_all()
        for network in networks:
            network = network._narrow(Fissures.IfNetwork.ConcreteNetworkAccess)
            attributes = network.get_attributes()
            net_list.append(attributes.id.network_code)
        return net_list


    def getStationIds(self, network_id=None):
        """
        Return all available stations as list.

        If no network_id is specified this may take a long time

        :param network_id: Limit stations to network_id
        """
        # Retrieve network informations
        if network_id == None:
            networks = self.netFind.retrieve_all()
        else:
            networks = self.netFind.retrieve_by_code(network_id)
        station_list = []
        for network in networks:
            network = network._narrow(Fissures.IfNetwork.ConcreteNetworkAccess)
            stations = network.retrieve_stations()
            for station in stations:
                station_list.append(station.id.station_code)
        return station_list

    def getCoordinates(self, network_id, station_id, datetime):
        """
        Get Coordinates of a station.
        Still lacks a correct selection of metadata in time!

        >>> from obspy.fissures import Client
        >>> client = Client()
        >>> client.getCoordinates(network_id="GR", station_id="GRA1",
        ...                       datetime="2010-08-01")
        AttribDict({'latitude': 49.691886901855469, 'elevation': 499.5, 'longitude': 11.221719741821289})
        """
        sta = self._getStationObj(network_id=network_id, station_id=station_id,
                                  datetime=datetime)
        coords = AttribDict()
        loc = sta.my_location
        coords['elevation'] = loc.elevation.value
        unit = loc.elevation.the_units.name
        if unit != "METER":
            warnings.warn("Elevation not meter but %s." % unit)
        type = loc.type
        if str(type) != "GEOGRAPHIC":
            msg = "Location types != \"GEOGRAPHIC\" are not yet " + \
                  "implemented (type: \"%s\").\n" % type + \
                  "Please report the code that resulted in this error!"
            raise NotImplementedError(msg)
        coords['latitude'] = loc.latitude
        coords['longitude'] = loc.longitude
        return coords

    def getPAZ(self, network_id="GR", station_id="GRA1", channel_id="BHZ",
               datetime="2010-08-01"):
        """
        Get Poles&Zeros, gain and sensitivity of instrument for given ids and
        datetime.
        
        Useful links:
        http://www.seis.sc.edu/software/simple/
        http://www.seis.sc.edu/downloads/simple/simple-1.0.tar.gz
        http://www.seis.sc.edu/viewvc/seis/branches/IDL2.0/fissuresUtil/src/edu/sc/seis/fissuresUtil2/sac/SacPoleZero.java?revision=16507&view=markup&sortby=log&sortdir=down&pathrev=16568
        http://www.seis.sc.edu/viewvc/seis/branches/IDL2.0/fissuresImpl/src/edu/iris/Fissures2/network/ResponseImpl.java?view=markup&sortby=date&sortdir=down&pathrev=16174

        :param network_id: Network id, 2 char; e.g. "GE"
        :param station_id: Station id, 5 char; e.g. "APE"
        :type channel_id: String, 3 char
        :param channel_id: Channel id, e.g. "SHZ", no wildcards.
        :type datetime: :class:`~obspy.core.utcdatetime.UTCDateTime` or
                compatible String
        :param datetime: datetime of response information
        :return: :class:`~obspy.core.util.AttribDict`
        """
        if "*" in channel_id:
            msg = "Wildcards not allowed in channel_id"
            raise FissuresException(msg)
        net = self.netFind.retrieve_by_code(network_id)
        net = use_first_and_raise_or_warn(net, "network")
        sta = [sta for sta in net.retrieve_stations() \
               if sta.id.station_code == station_id]
        sta = use_first_and_raise_or_warn(sta, "station")
        cha = [cha for cha in net.retrieve_for_station(sta.id) \
               if cha.id.channel_code == channel_id]
        cha = use_first_and_raise_or_warn(cha, "channel")
        datetime = utcdatetime2Fissures(datetime)
        inst = net.retrieve_instrumentation(cha.id, datetime)
        resp = inst.the_response
        stage = use_first_and_raise_or_warn(resp.stages, "response stage")
        # XXX if str(stage.type) == "ANALOG":
        # XXX     multFac = 2 * math.pi
        # XXX else:
        # XXX     multFac = 1.0
        filters = [filter._v for filter in stage.filters \
                   if str(filter._d) == "POLEZERO"]
        filter = use_first_and_raise_or_warn(filters, "polezerofilter")
        paz = poleZeroFilter2PAZ(filter)
        norm = use_first_and_raise_or_warn(stage.the_normalization,
                                           "normalization")
        norm_fac = norm.ao_normalization_factor
        paz['gain'] = norm_fac
        paz['sensitivity'] = resp.the_sensitivity.sensitivity_factor
        #fs = response.getSensitivity().getFrequency();
        #sd *= Math.pow(2 * Math.PI * fs, gamma);
        #A0 = stage.getNormalization().getAoNormalizationFactor();
        #fn = stage.getNormalization().getNormalizationFreq();
        #A0 = A0 / Math.pow(2 * Math.PI * fn, gamma);
        #if str(stage.type) == "ANALOG":
            #A0 *= Math.pow(2 * Math.PI, pz.getPoles().length - pz.getZeros().length);
        #if(poles.length == 0 && zeros.length == 0)
        #    constant = (float)(sd * A0);
        #else
        #    constant = (float)(sd * calc_A0(poles, zeros, fs));
        return paz


    def _composeName(self, dc, interface):
        """
        Compose Fissures name in CosNaming.NameComponent manner. Set the
        dns, interfaces and objects together.
        
        >>> from obspy.fissures import Client
        >>> client = Client()
        >>> client._composeName(("/edu/iris/dmc", "IRIS_NetworkDC"),
        ...                     "NetworkDC") #doctest: +NORMALIZE_WHITESPACE
        [CosNaming.NameComponent(id='Fissures', kind='dns'),
         CosNaming.NameComponent(id='edu', kind='dns'),
         CosNaming.NameComponent(id='iris', kind='dns'),
         CosNaming.NameComponent(id='dmc', kind='dns'),
         CosNaming.NameComponent(id='NetworkDC', kind='interface'),
         CosNaming.NameComponent(id='IRIS_NetworkDC', kind='object_FVer1.0')]


        :param dc: Tuple containing dns and service as string
        :param interface: String describing kind of DC, one of EventDC,
            NetworkDC or DataCenter
        """
        # put network name together
        dns = [NameComponent(id='Fissures', kind='dns')]
        for id in dc[0].split('/'):
            if id != '':
                dns.append(NameComponent(id=id, kind='dns'))
        dns.extend([NameComponent(id=interface, kind='interface'),
                    NameComponent(id=dc[1], kind='object_FVer1.0')])
        return dns


    def _getChannelObj(self, network_id, station_id, location_id, channel_id):
        """
        Return Fissures channel object.
        
        Fissures channel object is requested from the clients network_dc.
        
        :param network_id: Network id, 2 char; e.g. "GE"
        :param station_id: Station id, 5 char; e.g. "APE"
        :param location_id: Location id, 2 char; e.g. "  "
        :param channel_id: Channel id, 3 char; e.g. "SHZ"
        :return: Fissures channel object
        """
        # retrieve a network
        net = self.netFind.retrieve_by_code(network_id)
        net = use_first_and_raise_or_warn(net, "network")
        net = net._narrow(Fissures.IfNetwork.ConcreteNetworkAccess)
        # retrieve channels from network
        if location_id.strip() == "":
            # must be two empty spaces
            location_id = "  "
        # Retrieve Channel object
        # XXX: wildcards not yet implemented
        return net.retrieve_channels_by_code(station_id, location_id,
                                             channel_id)

    def _getSeisObj(self, channel_obj, start_datetime, end_datetime):
        """
        Return Fissures seismogram object.
        
        Fissures seismogram object is requested from the clients
        network_dc. This actually contains the data.
        
        :param channel_obj: Fissures channel object
        :param start_datetime: UTCDateTime object of starttime
        :param end_datetime: UTCDateTime object of endtime
        :return: Fissures seismogram object
        """
        # Transform datetime into correct format
        t1 = utcdatetime2Fissures(start_datetime)
        t2 = utcdatetime2Fissures(end_datetime)
        # Form request for all channels
        request = [Fissures.IfSeismogramDC.RequestFilter(c.id, t1, t2) \
                   for c in channel_obj]
        # Retrieve Seismogram object
        return self.seisDC.retrieve_seismograms(request)

    def _getStationObj(self, network_id, station_id, datetime):
        """
        Return Fissures station object.
        
        Fissures station object is requested from the clients network_dc.
        
        :param network_id: Network id, 2 char; e.g. "GE"
        :param station_id: Station id, 5 char; e.g. "APE"
        :type datetime: String (understood by
                :class:`~obspy.core.datetime.DateTime`)
        :param datetime: Datetime to select station
        :return: Fissures channel object
        """
        net = self.netFind.retrieve_by_code(network_id)
        net = use_first_and_raise_or_warn(net, "network")
        # filter by station_id and by datetime (comparing datetime strings)
        datetime = UTCDateTime(datetime).formatFissures()
        stations = [sta for sta in net.retrieve_stations() \
                    if station_id == sta.id.station_code \
                    and datetime > sta.effective_time.start_time.date_time \
                    and datetime < sta.effective_time.end_time.date_time]
        return use_first_and_raise_or_warn(stations, "station")


if __name__ == '__main__':
    import doctest
    doctest.testmod(exclude_empty=True)
