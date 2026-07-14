# Rec. ITU-R S.1503-4 (09/2023)

> Extraído de `R-REC-S.1503-4-202309-I!!PDF-E-1.pdf` via pdftotext -layout. Figuras em `images/fig-<página>-<n>.png` (numeração de página = página física do PDF).



---

## [Página 1]

```text
                                  International Telecommunication Union
Recommendations                               Radiocommunication Sector




    Recommendation ITU-R S.1503-4
    (09/2023)
    S Series: Fixed-satellite service

    Functional description to be used in
    developing software tools for
    determining conformity of
    non-geostationary-satellite orbit
    fixed-satellite service systems or
    networks with limits contained in
    Article 22 of the Radio Regulations
```


---

## [Página 2]

```text
ii                                                Rec. ITU-R S.1503-4


                                                          Foreword

The role of the Radiocommunication Sector is to ensure the rational, equitable, efficient and economical use of the radio-
frequency spectrum by all radiocommunication services, including satellite services, and carry out studies without limit
of frequency range on the basis of which Recommendations are adopted.
The regulatory and policy functions of the Radiocommunication Sector are performed by World and Regional
Radiocommunication Conferences and Radiocommunication Assemblies supported by Study Groups.



                                   Policy on Intellectual Property Right (IPR)

ITU-R policy on IPR is described in the Common Patent Policy for ITU-T/ITU-R/ISO/IEC referenced in Resolution
ITU-R 1. Forms to be used for the submission of patent statements and licensing declarations by patent holders are
available from http://www.itu.int/ITU-R/go/patents/en where the Guidelines for Implementation of the Common Patent
Policy for ITU-T/ITU-R/ISO/IEC and the ITU-R patent information database can also be found.




                                           Series of ITU-R Recommendations
                                   (Also available online at https://www.itu.int/publ/R-REC/en)

     Series                                                           Title
     BO          Satellite delivery
     BR          Recording for production, archival and play-out; film for television
     BS          Broadcasting service (sound)
     BT          Broadcasting service (television)
     F           Fixed service
     M           Mobile, radiodetermination, amateur and related satellite services
     P           Radiowave propagation
     RA          Radio astronomy
     RS          Remote sensing systems
     S           Fixed-satellite service
     SA          Space applications and meteorology
     SF          Frequency sharing and coordination between fixed-satellite and fixed service systems
     SM          Spectrum management
     SNG         Satellite news gathering
     TF          Time signals and frequency standards emissions
     V           Vocabulary and related subjects




 Note: This ITU-R Recommendation was approved in English under the procedure detailed in Resolution ITU-R 1.



                                                                                                          Electronic Publication
                                                                                                                  Geneva, 2023


                                                          © ITU 2023
All rights reserved. No part of this publication may be reproduced, by any means whatsoever, without written permission of ITU.
```


---

## [Página 3]

```text
                                          Rec. ITU-R S.1503-4                                               1


                           RECOMMENDATION ITU-R S.1503-4
 Functional description to be used in developing software tools for determining
 conformity of non-geostationary-satellite orbit fixed-satellite service systems or
      networks with limits contained in Article 22 of the Radio Regulations
                                                                             (2000-2005-2013-2018-2023)

Scope
This Recommendation provides a functional description of the software for use by the Radiocommunication
Bureau of ITU to conduct examination of non-geostationary-satellite orbit (non-GSO) fixed-satellite service
(FSS) system notifications for their compliance with the validation limits specified in the Radio Regulations.


Keywords
epfd, non-GSO, methodology
Abbreviations/Glossary
Alpha angle ():        The minimum angle between the line to the non-GSO satellite and the lines to
                        the GSO arc as seen at the earth station communicating with a geostationary-
                        satellite orbit (GSO).
e.i.r.p. mask:          Equivalent isotropic radiated power mask used to define the emissions of the
                        non-GSO earth station in the epfd(up) calculation or the emissions of the non-
                        GSO satellite for the epfd(IS) calculation.
epfd:                   Equivalent power flux-density, as defined in Radio Regulations (RR)
                        No. 22.5C.1, of which there are three cases to consider:
                        epfd(down): emissions from the non-GSO satellite system into a GSO satellite
                        earth station;
                        epfd(up): emissions from the non-GSO earth station into a GSO satellite;
                        epfd(IS): inter-satellite emissions from the non-GSO satellite system into a
                        GSO satellite system.
pfd mask:               power flux-density mask used to define the emissions of the non-GSO satellite
                        in the epfd(down) calculation.
WCG:                    Worst Case Geometry, the location of the GSO earth station and GSO satellite
                        that analysis suggests would cause the highest single entry epfd values for
                        given inputs.
Related ITU Recommendations, Reports
Recommendation ITU-R BO.1443 – Reference BSS earth station antenna patterns for use in interference
      assessment involving non-GSO satellites in frequency bands covered by RR Appendix 30
Recommendation ITU-R S.672 – Satellite antenna radiation pattern for use as a design objective in the fixed-
      satellite service employing geostationary satellites
Recommendation ITU-R S.1428 – Reference FSS earth-station radiation patterns for use in interference
      assessment involving non-GSO satellites in frequency bands between 10.7 GHz and 30 GHz
```


---

## [Página 4]

```text
2                                     Rec. ITU-R S.1503-4

The ITU Radiocommunication Assembly,
        considering
a)      that WRC-2000 adopted, in Article 22 of the Radio Regulations (RR), single-entry limits
applicable to non-geostationary satellite orbit (non-GSO) fixed-satellite service (FSS) systems in
certain parts of the frequency range 10.7-30 GHz to protect geostationary satellite orbit (GSO)
networks operating in the same frequency bands from unacceptable interference;
b)      that these frequency bands are currently used or planned to be used extensively by
geostationary-satellite orbit systems (GSO systems);
c)      that during the examination under Nos. 9.35 and 11.31, the Bureau examines non-GSO FSS
systems to ensure their compliance with the single-entry epfd limits given in Tables 22-1A, 22-1B,
22-1C, 22-1D, 22-1E, 22-2 and 22-3 of Article 22 of the RR;
d)       that to perform the regulatory examination referred to in considering c), the
Radiocommunication Bureau (BR) requires a software tool that permits the calculation of the power
levels produced by such systems, on the basis of the specific characteristics of each non-GSO FSS
system submitted to the Bureau for coordination or notification, as appropriate;
e)      that GSO FSS and GSO broadcasting-satellite service (BSS) systems have individual
characteristics and that interference assessments will be required for multiple combinations of
antenna characteristics, interference levels and probabilities;
f)     that designers of satellite networks (non-GSO FSS, GSO FSS and GSO BSS) require
knowledge of the basis on which the BR will make such checks;
g)      that WRC-19 adapted a methodology which assesses the impact of interference from non-
GSO systems into GSO networks in the frequency range 37.5-51.4 GHz that utilises epfd statistics as
an input;
h)      that such tools may be already developed or under development and may be offered to
the BR,
        recommends
that the functional description specified in Annex 1 should be used to develop software tools
calculating the power levels produced by non-GSO FSS systems and the compliance of these levels
with the limits contained in Tables 22-1A, 22-1B, 22-1C, 22-1D, 22-1E, 22-2 and 22-3 of Article 22
of the RR.
NOTE – The ITU-R agreed to continue working on a further revision of this Recommendation and
has prepared a draft Workplan (in relation with this Recommendation) and a Working Document.
```


---

## [Página 5]

```text
                                                          Rec. ITU-R S.1503-4                                                                    3


                                                                    Annex 1

              Functional description of software for use by the BR in checking
                   compliance of non-GSO FSS systems with epfd limits
                                                        TABLE OF CONTENTS
                                                                                                                                              Page

Annex 1 – Functional description of software for use by the BR in checking compliance of
    non-GSO FSS systems with epfd limits .........................................................................                               3

PART A – Fundamental constraints and basic assumptions....................................................                                       5

A1      General............................................................................................................................      5

A2      Fundamental assumptions...............................................................................................                   7

A3      Modelling methodology .................................................................................................                  9

PART B – Input parameters .....................................................................................................                 10

B1      Introduction ....................................................................................................................       10

B2      BR supplied parameters to software ...............................................................................                      10

B3      Non-GSO system inputs to software ..............................................................................                        11

B4      pfd/e.i.r.p. masks.............................................................................................................         15

B5      Validation of input parameters .......................................................................................                  16

Attachment to Part B ................................................................................................................           18

PART C – Generation of pfd/e.i.r.p. masks .............................................................................                         25

C1      Definition ........................................................................................................................     25

C2      Generation of satellite pfd masks ...................................................................................                   25

C3      Generation of e.i.r.p. masks ............................................................................................               34

C4      pfd and e.i.r.p. mask format ............................................................................................               36

PART D – Software for the examination of non-GSO filings .................................................                                      46

D1      Introduction ....................................................................................................................       46

D2      Determination of runs to execute....................................................................................                    47

D3      Worst-case geometry ......................................................................................................              50

D4      Calculation of time step size and number of time steps .................................................                                81

D5      epfd calculation description ............................................................................................               94
```


---

## [Página 6]

```text
4                                                       Rec. ITU-R S.1503-4

D6     Geometry and algorithms ...............................................................................................            112

D7     Structure and format of results .......................................................................................            134

PART E – Testing of the reliability of the software outputs ...................................................                           136

E1     Evaluation of the computation accuracy of the candidate software ...............................                                   136

E2     Evaluation of the epfd(↓/↑) statistics obtained by the BR ..............................................                           136

E3     Verification of the pfd masks .........................................................................................            136

E4     Re-testing of the BR software after any modifications or upgrades...............................                                   137

PART F – Software implementing this Recommendation.......................................................                                 137

F1     Operating system ............................................................................................................      137

F2     Interfaces to existing software and databases .................................................................                    137

F3     User manual ....................................................................................................................   137
```


---

## [Página 7]

```text
                                         Rec. ITU-R S.1503-4                                              5

                                                 PART A

                          Fundamental constraints and basic assumptions


A1      General

A1.1    Purpose
The software algorithm described in this Annex is designed for its application by the BR to conduct
examination of the non-GSO FSS system notifications for their compliance with the limits contained
in Tables 22-1A, 22-1B, 22-1C, 22-1D, 22-1E, 22-2 and 22-3 of Article 22 of the RR.
The algorithm could also under certain conditions permit examination of whether coordination is
required between non-GSO FSS systems and large earth stations under Articles 9.7A and 9.7B using
the criteria in RR Appendix 5.
The algorithm uses the parameters of the non-GSO system to derive statistics of epfd. These epfd
statistics could be used as the input to other methodologies that assess interference from non-GSO
systems into GSO network(s) in bands where there are no epfd limits in Article 22 of the RR.
The algorithm in this Recommendation was developed based upon a reference GSO satellite in
equatorial orbit with zero inclination angle. The analysis to determine whether a non-GSO satellite
system meets the epfd limits in Article 22 of the RR is made by calculating the epfd levels at the
reference GSO satellite or at an earth station pointing towards it. A GSO satellite network operating
with non-zero inclination angles could receive higher epfd levels than those predicted by this
algorithm, noting that the analysis under RR Nos 22.5C, 22.5D, 22.5F are conducted assuming zero
inclination of the GSO satellite. Analysis under RR Nos. 9.7A and 9.7B, however, is to determine if
coordination is required by comparing against the trigger level in Appendix 5 of the RR, and therefore
in this case other methodologies, including those that assume non-zero GSO satellite inclination,
could be acceptable alternatives.

A1.2    Software block-diagram
The block-diagram of the software algorithm described in this Annex is shown in Fig. 1. It comprises
initial data and calculation for the notifying administration and the BR. The data section contains the
whole set of parameters relevant to the notified non-GSO system, a set of reference GSO system
parameters as well as epfd limits provided by the BR.
The calculation section is designed for estimations required to examine notified non-GSO systems
compliance with the epfd limits. The calculation section is based on a concept of a downlink power
flux-density (pfd) mask (see Note 1), an uplink effective isotropic radiated power (e.i.r.p.) mask (see
Note 2) and inter-satellite e.i.r.p. mask (see Note 3).
NOTE 1 – A pfd mask is a maximum pfd produced by a non-GSO space station and is defined in Part C.
NOTE 2 – An e.i.r.p. mask is a maximum e.i.r.p. radiated by a non-GSO earth station and is a function of
latitude and the angle from the boresight of the transmitting antenna main beam to a point on the GSO arc.
NOTE 3 – An inter-satellite e.i.r.p. mask is a maximum e.i.r.p. radiated by a non-GSO space station and is a
function of latitude and the angle between the line to the sub-satellite point and a point on the GSO arc.
The pfd/e.i.r.p. masks are calculated by the filing administration as identified in Block 1 and then
supplied with the other non-GSO system parameters in Blocks a and b. The BR supplies additional
parameters, in particular the epfd limits in Block c.
```


---

## [Página 8]

```text
6                                      Rec. ITU-R S.1503-4

                                                FIGURE 1
                               Stages in epfd verification – key logic blocks




A1.3    Allocation of responsibilities between Administrations and the BR for software
        employment
Taking into account significant complexity regarding specific features of different non-GSO system
configurations in the software it would seem appropriate to impose some burden of responsibility
relevant to testing for epfd limits on administrations notifying appropriate non-GSO systems.
Therefore the examination procedure for meeting epfd limits would consist of two stages. The first
stage would include derivation of a mask for pfd/e.i.r.p. produced by interfering non-GSO network
stations. The mask would account for all the features of specific non-GSO systems arrangements
(such as possible beam pointing and transmit powers). The first stage would be finalized by the
delivery of the pfd/e.i.r.p. mask to the BR.
```


---

## [Página 9]

```text
                                        Rec. ITU-R S.1503-4                                           7

The second stage calculations would be effected at the BR. The second stage would feature the
following operations:
–       Identification of the runs required for a non-GSO network taking into account the frequencies
        for which it has filed and the frequency ranges for which there are epfd limits in RR Article 22
        (Block 2).
–       Definition of the maximum epfd geometry of a GSO space station and an earth station of that
        network (Block 3). It would ensure verification of sharing feasibility for a notified non-GSO
        network with any GSO network in the FSS and BSS.
–       epfd statistics estimation (Block 4).
–       Making a decision on interference compliance with appropriate epfd limits.
The estimations are based on the non-GSO system parameters (Blocks a and b) delivered by
a notifying administration and the initial data (Block c) available at the BR.
Any administration may use software that uses the algorithms defined in this Annex together with
data on the non-GSO networks to estimate statistics for interference into its own GSO networks and
check for compliance with epfd limits. It would assist in solving probable disputes between the BR
and administrations concerned.
The elements of the software block-diagram discussed are presented hereinafter in detail. The Parts
are as follows:
Part A –    Basic limitations and main system requirements for the software as a whole are
            presented.
Part B –    Non-GSO networks parameters and initial data for Blocks a and b are discussed.
Part C –    Definitions and estimation algorithms for pfd/e.i.r.p. masks relative to non-GSO network
            earth and space stations are presented. Specifics of those masks applying in simulation
            is also discussed (Block 1).
Part D –    This Part deals with general requirements on the software related to examination of non-
             GSO networks notifications, algorithms to estimate epfd statistics, and the format for
             output data presentation. Part D covers issues of Blocks 2, 3 and 4.
Parts E, F – These Parts define requirements on the software related to valuation of delivered
              software and to verification of the software output on validity.


A2      Fundamental assumptions

A2.1    Units of measurement
To provide for adequate simulation results and to avoid errors a common measurement units system
is used in Table 1 for the software description. The list of measurement units for the basic physical
parameters is shown in Table 1.
```


---

## [Página 10]

```text
8                                          Rec. ITU-R S.1503-4

                                                  TABLE 1
                The system of measurement units for basic physical parameters used
                             for the software performance description
                                      Parameter                                                Units
 Distance                                                                                       km
 Angle                                                                                        degree
 Time                                                                                            s
 Linear rotation velocity                                                                      km/s
 Angular rotation velocity                                                                    degree/s
 Frequency                                                                                     MHz
 Frequency bandwidth                                                                           kHz
 Power                                                                                         dBW
 Power spectral density                                                                      dB(W/Hz)
 pfd                                                                                   dB(W/(m2 · BWref))
 Average number of co-frequency non-GSO earth stations per unit area                           1/km2
 epfd, epfd or epfdis                                                                      dB(W/BWref)
 Antenna gain                                                                                   dBi
 Geographical position on the Earth’s surface                                                 degrees


A2.2     Constants
The functional description of the software for examination of non-GSO networks notification at the
BR uses the constants shown in Table 2.

                                                  TABLE 2
                                      Constants to be used by software
                   Parameter                       Notation      Numerical value                     Units
 Radius of the Earth                                  Re               6 378.145                      km
 Radius of geostationary orbit                       Rgeo              42 164.2                       km
 Gravitational constant                                         3.986 012  10    5                 km3/s2
 Speed of light                                        c        2.997 924 58  105                   km/s
 Angular rate of rotation of the Earth                e      4.178 074 582 3  10     –3        degree/s
 Earth rotation period                                Te          86 164.090 54                        s
 Factor of the Earth non-sphericity                   J2          0.001 082 636                        –


A2.3     The Earth model
The force of the Earth attraction is the main factor to define a satellite orbital motion. Additional
factors include:
–        orbit variations due to the Earth’s oblateness and its mass distribution irregularities;
–        solar and lunar attraction;
```


---

## [Página 11]

```text
                                                   Rec. ITU-R S.1503-4                                           9

–          medium drag for satellite;
–          solar radiation pressure, etc.
The function description of software in this Annex accounts orbit perturbations only due to the Earth
oblateness. It is motivated by the fact that effect of other perturbing factors is significantly less. The
Earth’s oblateness causes secular and periodic perturbations of ascending node longitude and orbit
perigee argument. Section D6.3 describes expressions to account the Earth’s oblateness effect.
The orbits of some repeating ground tracks can be very sensitive to the exact orbit model used.
Administrations could also provide the BR with their own independently determined average
precession rates that could be used by the software instead of the values calculated using the equation
in § D6.3.

A2.4       Constellation types
The algorithm in this Recommendation has been developed to be applicable to at least the non-GSO
satellite systems shown in Table 3.
Constellations can contain sub-constellations with different orbit parameters and shape, but all sub-
constellation within a constellation have to be either repeating or non-repeating. If the constellation
is repeating then the repeat period specified must be appropriate for all non-GSO satellites, including
all sub-constellations.

                                                         TABLE 3
                                                  Orbit type classification
        Type                  Orbit shape                       Equatorial ?                Repeating ?
          A                     Circular                             No                          Yes
          B                     Circular                             No                          No
          C                     Circular                            Yes                          n/a
                                            (1)
          D                    Elliptical                            No                          Yes
                                            (1)
          E                    Elliptical                            No                          No
 (1)
       Assuming that the elliptical system has perigee and apogee at the extrema in latitude, i.e. with active
       arc at highest or lowest latitude.



A3         Modelling methodology
The approach described in this Annex involves time simulation in which interference levels are
evaluated on a time step by time step basis. Section D4 defines the method to calculate the size of
time steps and the total number of time steps to be used. That section also identifies an optional dual
time step approach to reduce run times without altering the resulting decision.
```


---

## [Página 12]

```text
10                                      Rec. ITU-R S.1503-4


                                               PART B
                                          Input parameters


B1      Introduction

B1.1    Background
Certain parameters for a non-GSO network and other data must be specified in order to accomplish
the requisite software functions:
–       Function 1: Provide the pfd masks for the non-GSO satellites (downlink) and the e.i.r.p. mask
        for the earth stations transmitting to those satellites (uplink) or non-GSO satellite
        (intersatellite).
–       Function 2: Apply the pfd/e.i.r.p. mask in the calculation of downlink epfd↓, uplink epfd↑
        and/or intersatellite epfd levels (cumulative time distributions of epfd).
–       Function 3: Determine whether the pfd/e.i.r.p. mask levels are consistent with basic
        transmission parameters of the non-GSO network, only in the case of dispute.
The roles of the administration of the non-GSO network and the BR are discussed in § A1.3.
Detailed parameters are needed by the BR in support of Function 2 and so this section focuses on the
parameters needed to fulfil that requirement.
The parameters provided should be consistent, so that if the administrations modify its network (e.g.
if there are changes to the constellation) so that the pfd/e.i.r.p. changes then a new mask would need
to be supplied to the BR.

B1.2    Scope and overview
This section identifies inputs to the software in four main sections:
–       Section B2 defines the inputs from the BR;
–       Section B3 defines the inputs from the non-GSO operator excluding the pfd/e.i.r.p. masks;
–       Section B4 defines the pfd/e.i.r.p. masks.
–       An Attachment to Part B then maps the parameters to the SRS database tables.
Note that in the following tables, square brackets in variable names indicate an index for that variable
and not tentative text.


B2      BR supplied parameters to software
The BR supplies two types of data, firstly the type of run to execute:

RunType                         One of {Article 22, 9.7A, 9.7B}
SystemID                        ID of system to examine (either non-GSO or large earth station)


The second data is to provide the threshold epfd levels to use as the pass / fail criteria. These are
accessed by the software when generating the runs and consists of a series of records as follows:

 epfddirection                  One of {Down, Up, IS}
 VictimService                  One of {FSS, BSS}
```


---

## [Página 13]

```text
                                        Rec. ITU-R S.1503-4                                                11

 StartFrequencyMHz              Start of frequency range for which epfd threshold applies
 EndFrequencyMHz                End of frequency range for which epfd threshold applies
 VictimAntennaType              Reference code for antenna pattern to be used in calls to ITU supplied
                                antenna gain pattern DLL
 VictimAntennaDishSize          Dish size of victim antenna pattern to be used in calls to ITU supplied
                                antenna gain pattern DLL
 VictimAntennaBeamwidth         Beamwidth of victim antenna pattern to be used in calls to ITU supplied
                                antenna gain pattern DLL
 RefBandwidthHz                 Reference bandwidth in Hz of epfd level
 NumPoints                      Number of points in epfd threshold mask
 epfdthreshold[N]               epfd level in dBW/m2/Reference bandwidth
 epfdpercent[N]                 Percentage of time associated with epfd threshold



B3       Non-GSO system inputs to software
These are split into constellation parameters, orbit parameters for each space station and one or more
sets of system operating parameters.

B3.1     Non-GSO constellation parameters
Nsat                          Number of non-GSO satellites
H_MIN                         Minimum operating height (km)
DoesRepeat                    Flag to identify that constellation repeats using station keeping to maintain
                              track
AdminSuppliedPrecession       Flag to identify that the constellation orbit model precession field is supplied
                              by the admin
Wdelta                        Station keeping range (degrees)
ORBIT_PRECESS                 Administration supplied precession rate (degrees/second)


B3.2     Non-GSO space station parameters
For each of the non-GSO satellites, the following parameters to define the location of the constellation
at the start of the simulation:

             A[N]               Semi-major axis of orbit (km)
             E[N]               Eccentricity of orbit
             I[N]               Inclination of orbit (degrees)
             O[N]               Longitude of ascending node of orbit (degrees)
             W[N]               Argument of perigee (degrees)
             V[N]               True anomaly (degrees)
```


---

## [Página 14]

```text
12                                    Rec. ITU-R S.1503-4

B3.3     Non-GSO system operating parameters
These represent the set of parameters required to define the non-GSO system’s operations. There
could be different sets of parameters at different frequency bands, but only one set of operating
parameters for any frequency band used by the non-GSO system.

Freq_Min                         Minimum frequency for which this set of parameters applies
Freq_Max                         Maximum frequency for which this set of parameters applies
                                 Exclusion zone angle (degrees), the minimum angle to the
                                 GSO arc at the non-GSO ES at which the non-GSO system can
                                 provide a service to that location, defined at the ES ( angle)
                                 by latitude. The MIN_EXCLUDE at a specific latitude will be
MIN_EXCLUDE[Latitude]
                                 derived using linear interpolation between data points.
                                 This field could vary between non-GSO system orbit planes
                                 via the orb_id field. If the orb_id field equals 0 then the data
                                 exclusion zone data applies to all orbit planes
MIN_ELEV[Latitude][Azimuth]      Minimum elevation angle of the non-GSO earth station when it is
                                 receiving or transmitting (degrees) by latitude and azimuth. The nearest
                                 latitude to that in the table will be used and then linear interpolation in
                                 azimuth
MIN_DURATION[Latitude]           Minimum satellite tracking duration at latitude (seconds): the nearest
                                 latitude to that in the table will be used
MAX_CO_FREQ[Latitude]            Maximum number of co-frequency tracked non-GSO satellites by
                                 latitude: the nearest latitude to that in the table will be used
ES_DENSITY                       Average number of non-GSO earth stations active at the same time
                                 (/km2)
ES_DISTANCE                      Average distance between cell or beam footprint centre (km)
ES_LAT_MIN                       Minimum limit of the latitude range of non-GSO ES (degrees)
ES_LAT_MAX                       Maximum limit of the latitude range of non-GSO ES (degrees)
MIN_ANGLE_AT_ES                  Minimum angle in degrees at the surface of the Earth between the lines
                                 to any two active non-GSO satellites. Assumed to be zero if not
                                 provided. Not applicable if the MIN_DURATION[Latitude] is non-zero
MIN_ANGLE_AT_SAT                 The minimum angle in degrees at the non-GSO satellite between the
                                 lines to any two active non-GSO earth stations. Assumed to be zero if
                                 not provided
MAX_CO_FREQ_SAT                  The maximum number of non-geostationary earth stations tracked co-
                                 frequency by a non-geostationary satellite within the EPFD(up)
                                 calculation. If a value is not provided, it is assumed that the maximum
                                 number of earth stations tracked co-frequency by a non-geostationary
                                 satellite is equal to the number of earth stations created for the epfd↑ run


These parameters would be supplied in XML format with header as follows:
 <non_gso_operating_parameters es_lat_max="+90" es_lat_min="-90" es_distance="200"
es_density="0.00001"     c_name="orb_id"     b_name="azimuth"        a_name="latitude"
high_freq_mhz="F2" low_freq_mhz="F1" param_id="1" angle_at_es = "5" min_angle_at_sat =
5>
where:
```


---

## [Página 15]

```text
                                         Rec. ITU-R S.1503-4                                         13


          Field                     Type or range                    Units               Example
 ntc_id               Integer                                          –      12345678
 sat_name             String                                           –      My satellite network
 param_id             Integer                                          –      1
 low_freq_mhz         Double precision                               MHz      10 000
 high_freq_mhz        Double precision                               MHz      12 000
 a_name               {latitude} range −90 to +90 degrees              –      latitude
 b_name               {azimuth} range 000 to 360 degrees               –      azimuth
 c_name               {orb_id} range 01 to 9999                        –      orb_id
                      0 should be used to indicate that parameters
                      will be applicable to all orbits
 es_density           Double precision                                km2     0.000 1
 es_distance          Double precision                                km      200
 es_lat_min           Double precision                               degree   −90
 es_lat_max           Double precision                               degree   +90
 min_angle_at_sat     Double precision range 0 to +90 degrees        degree   15
 min_angle_at_es      Double precision range 0 to +90 degrees        degree   5
 max_co_freq          Integer range 0 to 9999                          -      2
 max_co_freq_sat      Integer, range 0 to 9999                         -      2
 min_duration         Integer, range 0 to 99999                      second   400
 elev_angle           Double precision, range 0 to +90 degrees       degree   5


After the header the XML contains arrays of MIN_EXCLUDE which can vary by orb_id and latitude
while the MIN_DURATION, MAX_CO_FREQ values vary only by latitude. The MIN_ELEV array
can vary by both latitude and azimuth.
Note that if the non-GSO ES type defined in the e.i.r.p. mask is specific rather than typical, then the
fields es_density and es_distance are not used.
The non-GSO operating parameters would be stored in the same database as the pfd and e.i.r.p. masks.
An example non-GSO operating parameters XML file would therefore be:
<?xml version="1.0"?>
<satellite_system sat_name="MySatName" ntc_id="12345678">
 <non_gso_operating_parameters es_lat_max="+90" es_lat_min="-90" es_distance="200"
es_density="0.00001"     c_name="orb_id"      b_name="azimuth"        a_name="latitude"
high_freq_mhz="F2" low_freq_mhz="F1" param_id="1" min_angle_at_sat = "0" min_angle_at es
=0>
  <min_exclude c="1">
   <exclusion_zone_angle a="-75">0</exclusion_zone_angle>
   <exclusion_zone_angle a="-45">3</exclusion_zone_angle>
   <exclusion_zone_angle a="-15">5</exclusion_zone_angle>
   <exclusion_zone_angle a="15">5</exclusion_zone_angle>
   <exclusion_zone_angle a="45">3</exclusion_zone_angle>
```


---

## [Página 16]

```text
14                                  Rec. ITU-R S.1503-4

      <exclusion_zone_angle a="75">0</exclusion_zone_angle>
     </min_exclude>
     <min_exclude oc="2">
      <exclusion_zone_angle a="-75">0</exclusion_zone_angle>
      <exclusion_zone_angle a="-45">4</exclusion_zone_angle>
      <exclusion_zone_angle a="-15">6</exclusion_zone_angle>
      <exclusion_zone_angle a="15">6</exclusion_zone_angle>
      <exclusion_zone_angle a="45">6</exclusion_zone_angle>
      <exclusion_zone_angle a="75">0</exclusion_zone_angle>
     </min_exclude>
     <max_co_freq a="0">2</max_co_freq>
     <min_duration a="-50">400</min_duration>
     <min_duration a="0">1000</min_duration>
     <min_duration a="50">400</min_duration>
     <min_elev a="-30">
      <elev_angle b="0">30</elev_angle>
      <elev_angle b="90">40</elev_angle>
      <elev_angle b="280">30</elev_angle>
      <elev_angle b="370">40</elev_angle>
     </min_elev>
     <min_elev a="0">
      <elev_angle b="0">20</elev_angle>
      <elev_angle b="90">30</elev_angle>
      <elev_angle b="280">20</elev_angle>
      <elev_angle b="370">30</elev_angle>
     </min_elev>
     <min_elev a="30">
      <elev_angle b="0">30</elev_angle>
      <elev_angle b="90">40</elev_angle>
      <elev_angle b="280">30</elev_angle>
      <elev_angle b="370">40</elev_angle>
     </min_elev>
 </non_gso_operating_parameters>
</satellite_system>
```


---

## [Página 17]

```text
                                         Rec. ITU-R S.1503-4                                              15

B4       pfd/e.i.r.p. masks

B4.1     Non-GSO downlink pfd mask

FreqMin                          Minimum of frequency range in MHz for this pfd mask
FreqMax                          Maximum of frequency range in MHz for this pfd mask
RefBW                            The power level in the pfd mask should be given in kHz with respect to the
                                 same reference bandwidth as the epfd thresholds in the tables in Article 22
                                 relevant for the frequency ranges covered. If the tables in Article 22 give
                                 two reference bandwidths (e.g. 40 kHz and 1 MHz) then the smaller
                                 bandwidth should be used.
MaskType                         One of {, or (az,el)}
Option 1                         The pfd mask is defined by:
pfd_mask (satellite,             – the non-GSO satellite
latitude, , L)                 – the latitude of the non-GSO sub-satellite point
                                 – the separation angle  between this non-GSO space station and the GSO
                                   arc, as defined in § D6.4.4
                                 – the difference L in longitude between the non-GSO sub-satellite point
                                   and the point on the GSO arc where the  angle is minimised as defined
                                   in § D6.4.4
Option 2                         The pfd mask is defined by:
pfd_mask (satellite,             – the non-GSO satellite
latitude, Az, El)                – the latitude of the non-GSO sub-satellite point
                                 – the azimuth angle, defined in § D6.4.5
                                 – the elevation angle, defined in § D6.4.5


B4.2     Non-GSO uplink e.i.r.p. mask

 FreqMin                         Minimum of frequency range in MHz for this pfd mask
 FreqMax                         Maximum of frequency range in MHz for this pfd mask
 RefBW                           The power level in the e.i.r.p. mask should be given in kHz with respect
                                 to the same reference bandwidth as the epfd thresholds in the tables in
                                 Article 22 relevant for the frequency ranges covered. If the tables in
                                 Article 22 give two reference bandwidths (e.g. 40 kHz and 1 MHz) then
                                 the smaller bandwidth should be used.
 ES_ID                           Reference of non-GSO ES or –1 if using generic ES
 MaskFormat                      Either LatitudeAndAngleGSOArc or
                                 LatitudeAzimuthElevationDeltaLong
 ES_e.i.r.p. [Lat] []           Non-GSO earth station e.i.r.p. as a function of latitude and the angle
                                 between the line from the non-GSO ES boresight line and the line from
                                 the non-GSO ES to a point on the GSO arc.
 ES_e.i.r.p.[Lat][Az][El][Delt   Non-GSO ES e.i.r.p. as a function of non-GSO ES latitude, current
 aLongES]                        pointing (azimuth, elevation) and difference in longitude between non-
                                 GSO ES and point on the GSO arc
```


---

## [Página 18]

```text
16                                      Rec. ITU-R S.1503-4

B4.3    Non-GSO inter-satellite e.i.r.p. mask

FreqMin                         Minimum of frequency range in MHz for this e.i.r.p. mask
FreqMax                         Maximum of frequency range in MHz for this e.i.r.p. mask
RefBW                           The power level in the e.i.r.p. mask should be given in kHz with respect to
                                the same reference bandwidth as the epfd thresholds in the Tables in
                                Article 22 relevant for the frequency ranges covered. If the Tables in
                                Article 22 give two reference bandwidths (e.g. 40 kHz and 1 MHz) then the
                                smaller bandwidth should be used.
SAT_e.i.r.p.[Lat] []           Non-GSO satellite e.i.r.p. as a function of latitude and the angle seen from
                                the non-GSO satellite between the non-GSO sub-point and a point on the
                                GSO arc.


B5      Validation of input parameters
This section describes the minimum met of validation of the input parameters: additional checks could
also be undertaken.

B5.1    Non-GSO space station parameters
This methodology is applicable for the types of non-GSO systems with orbital characteristics as
defined in Table 3. To ensure consistency with this assumption, the following tests are required to be
undertaken for each non-GSO satellite:
Test for circular or near-circular orbit:
        If the e > 0 and e < MAX_CIRCULAR_E then
        {
             WarningMessage: setting orbit to be circular from eccentricity = e
             Set e = 0 and continue
        }
Test HEO system has  = ±/2:
        If the eccentricity >= MAX_CIRCULAR_E then
        {
             Ensure w in range {−, +}
             If (abs(/2 – abs(w))) > MAX_HELO_DELTAW
             {
                 ErrorMessage: orbit apogree not at maximum latitude
                 Exit
             }
It is assumed that:
        MAX_CIRCULAR_E = 0.01
        MAX_HELO_DELTAW = 1e-5 degrees
With systems that use multiple sub-constellations, it must be checked that all are repeating or all are
non-repeating.
```


---

## [Página 19]

```text
                                      Rec. ITU-R S.1503-4                                        17

B5.2    Non-GSO system operating parameters ranges
The following non-GSO system operating parameters are to be checked:
        MIN_EXCLUDE[Latitude]  0
        MIN_ELEV[Latitude, Azimuth]  0
        MIN_DURATION[Latitude]  1 second
        MAX_CO_FREQ[Latitude]  0
        ES_DENSITY > 0
        ES_DISTANCE  0 (ES_DISTANCE = 0 means that only one earth station can be distributed
        inside the victim GSO footprint)
        +90º > ES_LAT_MIN  −90º
        +90º  ES_LAT_MAX > −90º
        ES_LAT_MAX > ES_LAT_MIN
        MIN_ANGLE_AT_ES  0
        MIN_ANGLE_AT_SAT  0
        MAX_CO_FREQ_SAT  0

B5.3    Masks and system operating parameters XML files
The following non-GSO system operating parameters are to be checked:
–       That there is only a single non-GSO system operating parameters set per frequency range.
–       That there is a non-GSO system operating parameters set for each frequency range that is to
        be examined.
–       That if the MIN_EXCLUDE varies by orbit plane, that a value is defined for each orbit plane.
It is also necessary to check that any ES e.i.r.p. masks using the LatitudeAndAngleGSOArc format
are monotonically decreasing.
```


---

## [Página 20]

```text
18                                      Rec. ITU-R S.1503-4


                                               Attachment
                                                to Part B

This Attachment to Part B details the parameters that the epfd software uses from the SRS database.
If the extended set of system operating parameters are provided, then values should be taken from
that XML file rather than the relevant SRS table.
Table 4 lists the current RR Appendix 4 information for non-GSO satellite systems included in the
BR space networks system (SNS) database. The relationship between the database tables is shown in
Fig. 2. Mask information and link tables are not shown in Fig. 2 but are described in Table 4.
Format description

       Value                                             Description
         X           Used to describe alphanumeric data.
                     e.g. X(9) specifies a 9-character field containing alphanumeric data
                     XXX is equivalent to X(3).
         9           Used to describe digits
        ‘.’          Shows the position of a decimal point
         S           Implies a sign (sign leading separate)
                     e.g. S999.99 implies a numeric field with a range of values from –999.99 to +999.99
                     99 implies a numeric field with a range of values from 0 to 99
```


---

## [Página 21]

```text
    Rec. ITU-R S.1503-4                19

             FIGURE 2
Extract from SRS entity relationship
```


---

## [Página 22]

```text
20                                     Rec. ITU-R S.1503-4

                                            TABLE 4
                                    SRS data for epfd analysis
Notice

     Data Item    Data Type   Format                  Description                              Validation
       ntc_id      Number      9(9)      Unique identifier of the notice                      Primary Key
     ntc_type       Text        X        Code indicating if the notice is of a                value != Null
                                         geostationary satellite [G],
                                         Non-geostationary       satellite [N],
                                         specific earth station [S] or typical
                                         earth station [T]
       d_rcv      Date/Time    9(8)      Date of receipt of the notice
      ntf_rsn       Text        X        Code indicating that the notice has        The software looks a value that is
                                         been submitted under RR1488 [N],           ‘C’ or ‘N’
                                         RR1060 [C], RR1107 [D], 9.1 [A], 9.6
                                         [C], 9.7A [D], 9.17 [D], 11.2 [N],
                                         11.12 [N], AP30/30A-Articles 2A, 4
                                         and 5 [B], AP30B-Articles 6 and 7 [P],
                                         AP30B-Article 8 [N] or Res49 [U]
       st_cur       Text       XX        Current processing status of the notice    The software looks for a value that
                                                                                    is ‘50’ in the Article 9.7A check

Non-geo

     Data Item    Data Type   Format                  Description                              Validation
       ntc_id      Number      9(9)      Unique identifier of the notice                      Primary Key
     sat_name       Text      X(20)      Name of the satellite
     nbr_sat_td    Number       9        Nco for the uplink case
      density      Number      9(9)      Average density of non-GSO ES for
                                         the uplink case
      avg_dist     Number      9(9)      Average distance between co-
                                         frequency non-GSO ES for the uplink
                                         case
     f_x_zone       Text        X        Should be ‘Y’ for alpha based
                                         exclusion zone
      x_zone       Number      9(9)      Size of exclusion zone

orbit

     Data Item    Data Type   Format                  Description                              Validation
       ntc_id      Number      9(9)      Unique identifier of the notice                       Foreign Key
      orb_id       Number       99       Sequence number of the orbital plane                 Primary Key
     nbr_sat_pl    Number       99       Number of satellites per           non-       value != Null && value > 0
                                         geostationary orbital plane
     right_asc     Number     999.99     Angular separation in degrees                        value != Null
                                         between the ascending node and the
                                         vernal equinox
     inclin_ang    Number     999.9      Inclination angle of the satellite orbit             value != Null
                                         with respect to the plane of the
                                         Equator
```


---

## [Página 23]

```text
                                          Rec. ITU-R S.1503-4                                                          21

orbit (continued)
   Data Item        Data Type   Format                   Description                              Validation
     apog            Number     9(5).99     The farthest altitude of the non-            value != Null && value > 0
                                            geostationary satellite above the
                                            surface of the Earth or other reference
                                            body – expressed in kilometres
                                            Distances > 99 999 km are expressed
                                            as a product of the values of the fields
                                            “apog” and “apog_exp” (see below)
                                            e.g. 125 000 = 1.25 × 105
   apog_exp          Number       99        Exponent part of the apogee expressed        value != Null && value >= 0
                                            in power of 10
                                            To indicate the exponent; give 0 for
                                            100, 1 for 101, 2 for 102, etc.
     perig           Number     9(5).99     The nearest altitude of the non-             value != Null && value > 0
                                            geostationary satellite above the
                                            surface of the Earth or other reference
                                            body – expressed in kilometres
                                            Distances > 99 999 km are expressed
                                            as a product of the values of the fields
                                            “perigee” and “perig_exp” (see
                                            below) e.g. 125 000 = 1.25 × 105
   perig_exp         Number       99        Exponent part of the perigee expressed       value != Null && value >= 0
                                            in power of 10
                                            To indicate the exponent; give 0 for
                                            100, 1 for 101, 2 for 102, etc.
   perig_arg         Number     999.9       Angular separation (degrees) between
                                            the ascending node and the perigee of
                                            an elliptical orbit.
                                            If RR No. 9.11A applies
     op_ht           Number     99.99       Minimum operating height of the non-         value != Null && value > 0
                                            geostationary satellite above the
                                            surface of the Earth or other reference
                                            body – expressed in kilometres
                                            Distances > 99 km are expressed as a
                                            product of the values of the fields
                                            “op_ht” and “op_ht_exp” (see below)
                                            e.g. 250 = 2.5 × 102
   op_ht_exp         Number       99        Exponent part of the operating height        value != Null && value >= 0
                                            expressed in power of 10
                                            To indicate the exponent; give 0 for
                                            100, 1 for 101, 2 for 102, etc.
   f_stn_keep         Text        X         Flag indicating if the space station       value != Null && (value == ‘Y’ ||
                                            uses [Y] or does not use [N] station-                    ‘N’)
                                            keeping to maintain a repeating
                                            ground track
   rpt_prd_dd        Number      999        Day part of constellation repeat period
                                            (s)
   rpt_prd_hh        Number       99        Hour part of constellation repeat
                                            period (s)
  rpt_prd_mm         Number       99        Minute part of constellation repeat
                                            period (s)
   rpt_prd_ss        Number       99        Second part of constellation repeat
                                            period (s)
   f_precess          Text        X         Flag indicating if the space station              value != Null &&
                                            should [Y] or should not [N] be                  (value == ‘Y’ || ‘N’)
                                            modelled with specific precession rate
                                            of the ascending node of the orbit
                                            instead of the J2 term
```


---

## [Página 24]

```text
22                                        Rec. ITU-R S.1503-4

orbit (end)
     Data Item    Data Type   Format                     Description                             Validation
     precession    Number      999.99       For a space station that is to be          If f_precess == ‘Y’ then value !=
                                            modelled with specific precession rate            Null && value > = 0
                                            of the ascending node of the orbit
                                            instead of the J2 term, the precession
                                            rate in degrees/day measured counter-
                                            clockwise in the equatorial plane
      long_asc     Number      999.99       Longitude of the ascending node for         value != Null && value > = 0
                                            the jth orbital plane measured counter-
                                            clockwise in the equatorial plane from
                                            the Greenwich meridian to the point
                                            where the satellite orbit makes its
                                            south-north crossing of the equatorial
                                            plane
                                            (0° = j < 360°)
     keep_rnge     Number       99.9        Longitudinal tolerance of           the    If f_stn_keep == ‘Y’ then value
                                            longitude of the ascending node                 != Null && value > = 0


Phase

     Data Item    Data Type   Format                     Description                             Validation
       ntc_id      Number       9(9)        Unique identifier of the notice                      Foreign Key
       orb_id      Number        99         Sequence number of the orbital plane                 Foreign Key
     orb_sat_id    Number        99         Satellite sequence number in the            value != Null && value > = 0
                                            orbital plane
     phase_ang     Number      999.9        Initial phase angle of the satellite in     value != Null && value > = 0
                                            the orbital plane
                                            If RR No. 9.11A applies

Grp

     Data Item    Data Type   Format                     Description                             Validation
       ntc_id      Number       9(9)        Unique identifier of the notice                      Foreign Key
       grp_id      Number       9(9)        Unique identifier of the group                       Primary Key
      emi_rcp       Text         X          Code identifying a beam as either                 value != Null &&
                                            transmitting [E] or receiving [R]                (value == ‘E’ || ‘R’)
     beam_name      Text        X(8)        Designation of the satellite antenna
                                            beam
      elev_min     Number     S9(3).99      Minimum elevation angle at which            value != Null && value > = 0
                                            any associated earth station can
                                            transmit to a non-geostationary
                                            satellite
                                            or minimum elevation angle at which
                                            the radio astronomy station conducts
                                            single-dish or VLBI observations
      freq_min     Number     9(6).9(6)     Minimum frequency in MHz                     value != Null && value > 0
                                            (assigned frequency – half bandwidth)
                                            (of all frequencies for this group)
     freq_max      Number     9(6).9(6)     Maximum frequency in MHz                     value != Null && value > 0
                                            (assigned frequency + half bandwidth)
                                            (of all frequencies for this group)
       d_rcv      Date/Time     9(8)        Date of receipt of the list of frequency
                                            assignments pertaining to the group
      noise_t      Number       9(6)        Receiving system noise temperature         Only validated for 9.7A/B checks
```


---

## [Página 25]

```text
                                      Rec. ITU-R S.1503-4                                                              23



srv_cls

   Data Item     Data Type   Format                  Description                              Validation
     grp_id       Number      9(9)      Unique identifier of the group                       Foreign Key
    seq_no        Number      9(4)      Sequence number                             value != Null && value > = 0
    stn_cls        Text       XX        Class of station

sat_oper

   Data Item     Data Type   Format                  Description                              Validation
     ntc_id       Number      9(9)      Unique identifier of the group                       Foreign Key
     lat_fr       Number      9(9)      Start of latitude range                         −90 <= lat_fr < lat_to
     lat_to       Number      9(9)      End of latitude range                            lat_fr < lat_to <= 90
   nbr_op_sat     Number       9        Nco for the down direction to use for          >0
                                        latitudes in the range lat_fr to lat_to

mask_info

   Data Item     Data Type   Format                  Description                              Validation
     ntc_id       Number      9(9)      Unique identifier of the group                       Foreign Key
    mask_id       Number      9(4)      Sequence number                                      Foreign Key
    f_mask         Text        X        Code identifying a mask as either earth           value != Null &&
                                        station e.i.r.p. ‘E’ for space station         (value == ‘E’ || ‘S’|| ‘P’)
                                        e.i.r.p. or ‘S’ for space station or ‘P”
                                        for pfd
  f_mask_type      Text        X        Code describing format of mask,                   value != Null &&
                                        where ‘A’ = Alpha, ‘Z’ =                    (value == ‘A’ || ‘Z’|| ‘O’||’D’)
                                        Azimuth/elevation , ‘O’ = off-axis and
                                        ‘D’ = azimuth, elevation and
                                        deltaLong

e_as_stn

   Data Item     Data Type   Format                  Description                              Validation
     grp_id       Number      9(9)      Unique identifier of the group                       Foreign Key
    seq_no        Number      9(4)      Sequence number                              value != Null && value >= 0
   stn_name        Text      X(20)      Name of the transmitting or receiving
                                        station
    stn_type       Text        X        Code indicating if the earth station is    value != Null && (value == ‘S’ ||
                                        specific ‘S’ or typical ‘T’                              ‘T’)
    bmwdth        Number     999.99     Angular width of radiation main lobe         value != Null && value > 0
                                        expressed in degrees with two decimal
                                        positions

mask_lnk1
    Data Item    Data Type   Format                  Description                             Validation
      grp_id      Number      9(9)       Unique identifier of the group                     Foreign Key
     mask_id      Number      9(4)       Unique identifier of the mask                      Foreign Key
      orb_id      Number       99        Sequence number of the orbital plane               Foreign Key
    sat_orb_id    Number       99        Satellite sequence number in the          value != Null && value > = 0
                                         orbital plane
```


---

## [Página 26]

```text
24                                      Rec. ITU-R S.1503-4

mask_lnk2

     Data Item    Data Type    Format                   Description                     Validation
      grp_id       Number       9(9)       Unique identifier of the group               Foreign Key
     seq_e_as      Number       9(4)       Sequence number of the associated            Foreign Key
                                           earth station
     mask_id       Number       9(4)       Unique identifier of the mask                Foreign Key

mask_lnk3

     Data Item    Data Type    Format                   Description                     Validation
        ntc_id     Number       9(9)       Unique identifier of the notice              Foreign Key
     param_id      Number       9(4)       Unique identifier of the system              Foreign Key
                                           operating parameters

Tables used in the Article 9.7A/9.7B calculations
e_stn

     Data Item    Data Type    Format                    Description                     Validation
        ntc_id     Number        9(9)       Unique identifier of the notice             Foreign Key
      stn_name       Text       X(20)       Name of the earth station                   value != Null
      sat_name       Text       X(20)       Name of the associated space station        value != Null
        lat_dec    Number     S9(2).9(4)    Latitude in degrees with four decimals      value != Null
      long_dec     Number     S9(2).9(4)    Longitude in degrees with four              value != Null
                                            decimals
      long_nom     Number      S999.99      Nominal longitude of the associated         value != Null
                                            space station, give '–' for West, '+' for
                                            East


e_ant

     Data Item    Data Type    Format                    Description                     Validation
        ntc_id     Number        9(9)       Unique identifier of the notice              Foreign key
      emi_rcp        Text         X         Code identifying a beam as either           value != Null
                                            transmitting [E] or receiving [R]
      bmwdth       Number       999.99      Beamwidth of the earth station
                                            antenna
         gain      Number       S99.9       Maximum isotropic gain of the earth
                                            station antenna
```


---

## [Página 27]

```text
                                        Rec. ITU-R S.1503-4                                           25

                                               PART C

                                  Generation of pfd/e.i.r.p. masks


C1      Definition
The purpose of generating pfd/e.i.r.p. masks is to define an envelope of the power radiated by the
non-GSO space stations and the non-GSO earth stations so that the results of calculations encompass
what would be radiated regardless of what resource allocation and switching strategy are used at
different periods of a non-GSO system life.
These masks are regulatory constraints in that the non-GSO FSS system should not exceed those
values at any time and could be derived using the methodology below. They represent an envelope
of the power produced by a system which will constrain how rapidly the pfd or e.i.r.p. can change
between data points and how low values for the extreme points in arrays can get. Note that it is feasible
for a system not to transmit at certain latitudes: in this case a null value of −1000 dBW should be
used.


C2      Generation of satellite pfd masks

C2.1    General presentation
The satellite pfd mask is defined by the maximum pfd generated by any space station in the interfering
non-GSO system as seen from any point at the surface of the Earth. A four dimensional pfd mask is
recommended for use by the BR verification software and is defined following one of the two options:
Option 1: As a function of:
–       the non-GSO satellite;
–       the latitude of the non-GSO sub-satellite point;
–       the separation angle  between this non-GSO space station and the GSO arc, as seen from
        any point on the surface of the Earth, as defined in § D6.4.4;
–       the difference L in longitude between the non-GSO sub-satellite point and the point on the
        GSO arc where the  angle is minimised, as defined in § D6.4.4.
Option 2: As a function of:
–       the non-GSO satellite;
–       the latitude of the non-GSO sub-satellite point;
–       the non-GSO satellite azimuth angle, defined in § D6.4.5;
–       the non-GSO satellite elevation angle, defined in § D6.4.5.
Whatever parameters are used to generate the pfd mask, the resulting pfd mask should be converted
to one of the format options above.
Because the non-GSO space station can generate simultaneously a given maximum number of beams,
it should be taken into account in order to better fit the system design and not be too constraining for
non-GSO systems.
The mitigation techniques used by the non-GSO system, such as the GSO arc avoidance,
are implemented in the calculation of the pfd mask. The GSO arc avoidance defines a non-operating
zone on the ground in the field of view of a non-GSO space station. The location of this non-operating
zone on the ground will move as a function of the latitude of the non-GSO sub-satellite point. To get
```


---

## [Página 28]

```text
26                                        Rec. ITU-R S.1503-4

a more accurate model of a non-GSO system, the latitude of the non-GSO sub-satellite point is taken
as a parameter to the pfd mask calculation.

C2.2     Mitigation techniques description
The mitigation technique implemented within the non-GSO system should be accurately explained
in this section in order to be fully modelled in the calculation of the epfd↓.
With regard to the use of a non-operating zone around the GSO arc, there are at least two different
ways of modelling a non-GSO system based on a cell architecture:
–       Cell-wide observance of a non-operating zone: a beam of a non-GSO space station is
        switched off if the separation angle between this non-GSO space station and the GSO arc, at
        any point of the non-GSO cell, is less than 0 (GSO arc avoidance angle).
–       Cell-centre observance of a non-operating zone: a beam of a non-GSO space station is
        switched off when the centre of the cell sees this non-GSO space station at less than 0 from
        the GSO arc.
Other mitigation techniques may be used by a non-GSO system which are not listed here. Information
on these techniques will be provided by the non-GSO administration for the description and
verification of the pfd mask.

C2.3     pfd calculation
C2.3.1 pfd calculation
The pfd radiated by a non-GSO space station at any point on the Earth’s surface is the sum of the pfd
produced by all illuminating beams in the co-frequency band.
Some non-GSO systems have tracking antennas which point to cells fixed on the Earth’s surface and
do not move with the spacecraft. However, since the pfd mask is generated with respect to
the non-GSO location, assumptions must be made in the development of the pfd mask. Making the
simplifying assumption that the cells move with the spacecraft can lead to inaccurate geographic
distributions of epfd levels.
As non-GSO systems use mitigation techniques, there will be no main beam-to-main beam alignment.
Therefore, de-polarization effects mean that both co-polarization and cross-polarization contributions
must be included as sources of interference.
The implementation of the pfd mask explicitly accounts for both co-polarization and
cross-polarization from non-GSO satellites into GSO earth stations for like types of polarization
(circular-to-circular or linear-to-linear). Isolation between systems of different types of polarization
(circular-to-linear) is not directly covered. A study has shown that the average total interference
power over all axial ratios and polarization ellipse orientations is a very small net increase in the
received interference power in the BSS antenna of 0.048 dB. The bounds of any cross-polarization
contributions, that are very unlikely to be reached, are from –30 dB to +3 dB.
Then:
                                      𝑁                    𝑁
                     𝑝𝑓𝑑 = 10 log(∑𝑖 𝑐𝑜 10𝑝𝑓𝑑_𝑐𝑜𝑖 /10 + ∑𝑗 𝑐𝑟𝑜𝑠𝑠 10𝑝𝑓𝑑_𝑐𝑟𝑜𝑠𝑠𝑗/10 )
where:
             pfd :   pfd radiated by a non-GSO space station (dB(W/m2)) in the reference bandwidth
               i:    index of the beams illuminated in the polarization considered
             Nco :   maximum number of beams which can be illuminated simultaneously in
                     the polarization considered
```


---

## [Página 29]

```text
                                          Rec. ITU-R S.1503-4                                           27

          pfd_coi :    pfd produced at the point considered at the Earth’s surface by one beam in
                       the polarization considered (dB(W/m2)) in the reference bandwidth
                 j:    index of the beams illuminated in the opposite polarization to the polarization
                       considered
            Ncross :   maximum number of beams which can be illuminated simultaneously in
                       the opposite polarization to the polarization considered
      pfd_crossj :     pfd produced at the point considered at the Earth’s surface by one beam in the
                       opposite polarization to the polarization considered (dB(W/m2)) in the reference
                       bandwidth
and
                                 𝑝𝑓𝑑_𝑐𝑜𝑖 = 𝑃𝑖 + 𝐺𝑖 − 10log10 (4 π 𝑑 2 )
where:
               Pi :    maximum power emitted by the beam i in the reference bandwidth
                       (dB(W/BWref))
           BWref :     reference bandwidth (kHz)
             Gi :      gain generated by the beam i in the polarization considered at the point
                       considered at the Earth’s surface (dBi)
                d:     distance between the non-GSO space station and the point considered at
                       the Earth’s surface (if the non-GSO satellite antenna gain is in isoflux, d is the
                       altitude of the non-GSO space station) (m)
and
                           𝑝𝑓𝑑_𝑐𝑟𝑜𝑠𝑠𝑗 = 𝑃𝑗 + 𝐺_𝑐𝑟𝑜𝑠𝑠𝑗 − 10log10 (4 π 𝑑 2 )
where:
         G_crossj :    cross-polarization gain generated by the beam j illuminated in the opposite
                       polarization to the polarization considered, at the point considered at the Earth’s
                       surface (dBi).
It is expected that the parameters used to generate the pfd/e.i.r.p. mask correspond to the performance
of the non-GSO satellite over its anticipated lifetime. The pfd levels should be an envelope over all
possible traffic and beam combinations and represent the peak pfd in the given direction feasible over
the lifetime of the system. Non-GSO satellite systems that employ adaptive antennas in which the
beam size and sidelobe can be adjusted should take the combination that would result in the highest
pfd expected during the anticipated lifetime in each direction when generating the pfd mask. The pfd
for all angles for which the satellite operates will therefore be the highest pfd that would be generated,
for example, when there is a traffic hot spot in that direction. This approach is consistent with the
algorithm in Part D to calculate the epfd which takes account of frequency re-use constraints within
the non-GSO system.
C2.3.2 Satellite antenna gain at the point considered at the Earth’s surface
The objective of this section is to determine the gain in the direction of a point M at the Earth’s surface
when the satellite antenna points towards a cell i. The antenna coordinate can be defined by four ways
of the coordinate system:
                :     spherical coordinate
                 v:    u = sin  cos , v = sin  sin 
                B:     A =  cos , B =  sin 
```


---

## [Página 30]

```text
28                                      Rec. ITU-R S.1503-4

        (Az, El) :   sin (El) = sin  sin , tan (Az) = tan  cos 
The antenna gain data can be provided in a number of formats and coordinate systems. As the key
input into the epfd calculation is the pfd mask, not the antenna gain pattern used to derive the pfd
mask, the format and coordinate system of this antenna gain pattern could be different from those
used elsewhere in this document. In particular, it is important to note that the above equations related
to the conversion between (θ, ) and (Az, El) are different from those included in § D3.1.3.1 and
which are used in the Worst-Case Geometry Algorithm.
As an example, the following calculations are performed in the antenna reference (A, B).
The sampling of the non-GSO antenna pattern should be adapted so that interpolation does not lead
to gain level significantly different from real values.
Figure 3 presents the geometry in the antenna plane (A, B), while Fig. 4 presents the geometry
between the (θ, ) and (Az, El) angles using the definitions above.

                                                FIGURE 3
                                           Antenna plane (A, B)
```


---

## [Página 31]

```text
                                              Rec. ITU-R S.1503-4                                            29

                                                       FIGURE 4
                (θ, φ) and (Az, El) geometry, illustrated using a reference frame centred at the satellite




The coordinates of the point M at the Earth’s surface are (a, b) in the antenna plane (A, B),
corresponding to (M, M) in the polar reference.
The coordinates of the point C centre of the cell i, are (Ac, Bc) in the antenna plane (A, B), and (c, c)
in the spherical reference.
For satellite antenna gain patterns with functional descriptions (i.e. equations), the gain into the point
M may be computed directly from the coordinates C(Ac, Bc) and M(a, b). For other patterns, the
satellite antenna gains are provided in a grid of (A, B) points, and the point M(a, b) may be located
between four points of the grid (A, B).
In general, it is therefore necessary to undertake interpolation between data points. Consider a grid of
values P for a range of x values = {x1, x2, …} and y values = {y1, y2,…} as in Fig. 5.
```


---

## [Página 32]

```text
30                                        Rec. ITU-R S.1503-4

                                                   FIGURE 5
                                        Interpolation between data points




The value of parameter P at point (x, y) can be derived by identifying the bounding values and hence:
                                                         𝑥−𝑥
                                                 λ𝑥 = 𝑥 − 𝑥1
                                                         2     1
                                                        𝑦 − 𝑦1
                                                 λ𝑦 = 𝑦 − 𝑦
                                                          2    1

Then P can be interpolated using:
              𝑃 = (1 − λ𝑥 )(1 − λ𝑦 )𝑃11 + λ𝑥 (1 − λ𝑦 )𝑃21 + (1 − λ𝑥 )λ𝑦 𝑃12 + λ𝑥 λ𝑦 𝑃22
The sampling of the non-GSO satellite antenna pattern should be adapted so that interpolation does
not lead to significant approximation.
The same criteria should be used when sampling the pfd mask.

C2.4     Methodology
The pfd mask is defined by the maximum pfd generated by any space station in the interfering
non-GSO system and as a function of the parameters defined either in option 1 or option 2. For the
generation of the pfd mask, the cells in the non-GSO satellite footprint are located according to the
beam pointing utilized by the non-GSO system. For satellites with steerable antennas, the satellite
can point to the same area of the Earth throughout its track through the sky.
These cells are fixed relative to the Earth’s surface. For satellites that have antenna-pointing angles
fixed relative to the satellite, the cell pattern is the same relative to the satellite but is moving relative
to the Earth.
C2.4.1 Option 1
Option 1 has been described for a pfd mask defined as a function of the separation angle ,
as an example.
The pfd mask is defined as a function of the separation angle  between this non-GSO space station
and the GSO arc, as seen from any point on the surface of the Earth, and the difference L in longitude
between the non-GSO sub-satellite point and the GSO satellite.
```


---

## [Página 33]

```text
                                          Rec. ITU-R S.1503-4                                           31

The angle  is therefore the minimum topocentric angle measured from this particular earth station
between the interfering non-GSO space station and any point in the visible GSO arc.
The objective of the mask is to define the maximum possible level of the pfd radiated by the non-GSO
space station as a function of the separation angle between the non-GSO space station and the GSO
arc at any point on the ground, per interval of L.
At each point of the non-GSO satellite footprint, the pfd value depends on:
–       the configuration of the spot beams which are illuminated by the satellite;
–       the maximum number of co-frequency beams which can be illuminated simultaneously;
–       the maximum number of co-frequency, co-polarization beams which can be illuminated
        simultaneously;
–       the maximum power available at the satellite repeater.
The proposed methodology for the generation of the pfd mask is explained in the following steps:
Step 1: At any given time, in the field of view of a non-GSO space station, Ntotal is the maximum
number of cells that can be seen with the minimum service elevation angle.
Step 2: In the field of view of the non-GSO space station, it is possible to draw iso- lines, i.e. the
points on the surface of the Earth which share the same value of  (see Figs 6 and 7).

                                                   FIGURE 6
                               Field of view of a non-GSO space station (Option 1)




Step 3: Along the iso- line, define intervals of L: difference in longitude between the non-GSO
sub-satellite point and the point on the GSO arc where the  angle is minimised.
Step 4: Per each interval of L, the iso- line can be defined by a set of n points M,k for k = 1, 2,...n.
To determine the maximum pfd corresponding to a given value of , it is necessary to calculate the
maximum pfd at each of the points M,k for k = 1, 2,...n. The maximum pfd at a given M,k is
determined by first finding the pfd contributed by each celli toward M,k taking into account the
dependency of the sidelobe patterns on the beam tilt angle. The maximum pfd contributions toward
M,k are then summed, with the number of contributions constrained by the physical limitations of
the space station:
```


---

## [Página 34]

```text
32                                      Rec. ITU-R S.1503-4

–       Out of the Ntotal cells that can be seen within the coverage area of the space station under
        a minimum elevation angle for communication, only Nco can be illuminated at the same
        frequency bandwidth, in one sense of polarization, and Ncross in the other sense of
        polarization. This characterizes the limitation of the antenna system on the non-GSO space
        station. To calculate the mask in one polarization, the cells which can be illuminated in the
        polarization concerned are identified, and the cross-polarization level is considered for other
        cells.
–       Out of these Nco and Ncross cells, only a given number can be powered simultaneously. This
        characterizes the limitation of the repeater system of the non-GSO space station.
–       If applicable, the limitations in terms of frequency reuse pattern and polarization reuse pattern
        also need to be clarified.
–       If applicable, the power allocated to one cell may vary taking into account the elevation angle
        relative to this cell, for example:

                                                FIGURE 7
                                        View in 3D of the iso- line




Step 5: The generation of the pfd mask also needs to take into account accurately the mitigation
technique implemented within the non-GSO system.
With regard to the use of a non-operating zone around the GSO arc, there are three different ways of
modelling a non-GSO system based on a cell architecture:
–       cell-wide observance of a non-operating zone: a beam is switched off when one point on the
        Earth sees a non-GSO satellite within 0 of the GSO arc. In this particular case, any beam
        illuminating a cell which is crossed by an iso- line corresponding to a value   0 is
        switched off;
–       cell-centre observance of a non-operating zone: a beam is switched off when the centre of
        the cell sees a non-GSO satellite within 0 of the GSO arc. In this case, any beam illuminating
        a cell with its centre inside the non-operating zone bounded by the two iso-0 lines is
        switched off;
Step 6: The maximum pfd value corresponding to a given value  within an interval of L is:
```


---

## [Página 35]

```text
                                         Rec. ITU-R S.1503-4                                         33


                               𝑝𝑓𝑑(α, ∆𝐿) = max1,2,…𝑛 (𝑝𝑓𝑑(𝑀α,𝑘 ))

Step 7: The location of an iso- line, hence the value of the maximum pfd along this line depends on
the latitude of the non-GSO sub-satellite point. Therefore, a set of pfd masks will need to be provided,
each corresponding to a given latitude of the sub-satellite point.
Step 8: A set of pfd masks may be needed (one per non-GSO satellite).
C2.4.2 Option 2
The pfd mask is defined in a grid in azimuth and elevation, per latitude of the non-GSO sub-satellite
as shown in Fig. 8.
The objective of the mask is to define the maximum possible level of the pfd radiated by the non-GSO
space station in this azimuth elevation grid.
At each point of the non-GSO satellite footprint, the pfd value depends on:
–       the configuration of the spot beams which are illuminated by the satellite;
–       the maximum number of co-frequency beams which can be illuminated simultaneously;
–       the maximum number of co-frequency, co-polarization beams which can be illuminated
        simultaneously;
–       the maximum power available at the satellite repeater.

                                                  FIGURE 8
                              Field of view of a non-GSO space station (Option 2)




The proposed methodology for the generation of the pfd mask is explained in the following steps:
Step 1: At any given time, in the field of view of a non-GSO space station, Ntotal is the maximum
number of cells that can be seen with the minimum service elevation angle.
Step 2: For each point M(Az, El), determine the maximum pfd. The maximum pfd at a given M,k is
determined by first finding the pfd contributed by each celli toward M(Az, El) taking into account the
dependency of the sidelobe patterns on the beam tilt angle. The maximum pfd contributions toward
M,k are then summed, with the number of contributions constrained by the physical limitations of
the space station:
```


---

## [Página 36]

```text
34                                      Rec. ITU-R S.1503-4

–       Out of the Ntotal cells that can be seen within the coverage area of the space station under a
        minimum elevation angle for communication, only Nco cells can be illuminated at the same
        frequency bandwidth, in one sense of polarization, and Ncross cells in the other sense of
        polarization. This characterizes the limitation of the antenna system on the non-GSO space
        station. To calculate the mask in one polarization, the cells which can be illuminated in the
        polarization concerned are identified, and the cross-polarization level is considered for other
        cells.
–       Out of these Nco and Ncross cells, only a given number can be powered simultaneously. This
        characterizes the limitation of the repeater system of the non-GSO space station.
–       If applicable, the limitations in terms of frequency reuse pattern and polarization reuse pattern
        also need to be clarified.
–       If applicable the power allocated to one cell may vary taking into account the elevation angle
        relative to this cell, for example.
Step 3: The generation of the pfd mask also needs to take into account accurately the mitigation
technique implemented within the non-GSO system.
With regard to the use of a non-operating zone around the GSO arc, there are three different ways of
modelling a non-GSO system based on a cell architecture:
–       cell-wide observance of a non-operating zone: a beam is switched off when one point on the
        Earth sees a non-GSO satellite within 0 of the GSO arc. In this particular case, any beam
        illuminating a cell which is crossed by an iso- line corresponding to a value   0 is
        switched off;
–       cell-centre observance of a non-operating zone: a beam is switched off when the centre of
        the cell sees a non-GSO satellite within 0 of the GSO arc. In this case, any beam illuminating
        a cell with its centre inside the non-operating zone bounded by the two iso-0 lines is
        switched off.
Step 4: A set of pfd masks may need to be provided as a function of the latitude of the sub-satellite
point.
Step 5: A set of pfd masks may be needed (one per non-GSO satellite).


C3      Generation of e.i.r.p. masks

C3.1    Generation of earth station e.i.r.p. masks
C3.1.1 General presentation
The earth station e.i.r.p. mask is defined by a set of tables of the maximum e.i.r.p. as a function of
latitude and one of:
1)       The off-axis angle in the direction of the GSO arc as generated by an earth station. There can
         be different such e.i.r.p. tables applicable at different latitudes.
2)       The azimuth and elevation of the non-GSO satellite that the earth station is currently pointing
         at and difference in longitude between the earth station and reference point on the GSO arc.
         There can be different such e.i.r.p. tables applicable at different latitudes.
The non-GSO earth station is located in a non-GSO cell which is served by a maximum number of
non-GSO space stations. The density of non-GSO earth stations which can operate co-frequency
simultaneously is also used as an input to the calculation.
```


---

## [Página 37]

```text
                                         Rec. ITU-R S.1503-4                                            35

C3.1.2 Mitigation techniques description
The mitigation technique implemented within the non-GSO system should be accurately explained
in this section in order to be fully modelled in the calculation of the epfd↑ (see § C2.2).
C3.1.3 Earth station antenna pattern
The earth station antenna pattern used needs to be identified to calculate the earth station e.i.r.p. mask.
C3.1.4 Methodology
Step 1:
The earth station gain pattern could be defined as a function of the off-axis angle at the non-GSO
earth station between the boresight direction towards the non-GSO space station and a point on the
GSO arc, and its e.i.r.p mask could be calculated as follows:
The earth station e.i.r.p. mask at a given latitude is defined by the maximum e.i.r.p. radiated in the
reference bandwidth by the earth station as a function of the off-axis angle, and is given by:
                              𝐸𝑆_ 𝑒. 𝑖. 𝑟. 𝑝. (𝐿𝑎𝑡, θ) = 𝐺(𝐿𝑎𝑡, θ) + 𝑃(𝐿𝑎𝑡)
where:
ES_e.i.r.p.(Lat, ): equivalent isotropic radiated power in the reference bandwidth (dB(W/BWref))
              Lat: latitude of ES for that e.i.r.p. mask
                  :   off-axis angle at the non-GSO earth station between the boresight line towards
                       non-GSO space station and a point on the GSO (e.g. the GSO space station)
                       (degrees)
          G(Lat, ):   earth station directional antenna gain (dBi) at given latitude
            P(Lat):    maximum power delivered to the antenna, in the reference bandwidth
                       (dB(W/BWraf)) at given latitude
             BWraf:    reference bandwidth (kHz).
Alternatively, the gain pattern could be provided in the format ES_e.i.r.p.(Lat, az, el, DeltaLongES)
and the corresponding e.i.r.p mask could be calculated as follows:
The earth station e.i.r.p. mask at a given latitude is defined by the maximum e.i.r.p. radiated in the
reference bandwidth by the earth station as a function of the azimuth, elevation and delta longitude
angles, and is given by:
                𝐸𝑆_ 𝑒. 𝑖. 𝑟. 𝑝. (𝐿𝑎𝑡, 𝑎𝑧, 𝑒𝑙, ∆𝐿𝑜𝑛𝑔𝐸𝑆) = 𝐺(𝐿𝑎𝑡, 𝑎𝑧, 𝑒𝑙, ∆𝐿𝑜𝑛𝑔) + 𝑃(𝐿𝑎𝑡)
where:
ES_e.i.r.p.(Lat, az, el, longES) : equivalent isotropic radiated power in the reference bandwidth
                      (dB(W/BWref))
             Lat : latitude of ES for that e.i.r.p. mask
              az : azimuth component of the antenna pointing direction (degree)
               el : elevation component of the antenna pointing direction (degree)
          LongES :    difference in longitude between non-GSO earth station and point on the GSO arc
                       (degree)
G(Lat, az, el, Long) :     earth station directional antenna gain (dBi) at given latitude
          P(Lat) : maximum power delivered to the antenna, in the reference bandwidth
                    (dB(W/BWraf)) at given latitude
           BWraf : reference bandwidth (kHz).
```


---

## [Página 38]

```text
36                                       Rec. ITU-R S.1503-4

Step 2: Assuming that the non-GSO cells are uniformly distributed on the Earth’s surface, the
simultaneous co-frequency transmit non-GSO earth stations are evenly distributed over the cell.
Therefore the interferer can be located at the centre of the cell to perform the simulation.
This exercise would be repeated for all latitudes for which the ES_e.i.r.p. could be different.

C3.2    Generation of space station e.i.r.p. masks
The space station e.i.r.p. mask is defined by the maximum e.i.r.p. generated by a non-GSO space
station as a function of the angle seen from the non-GSO space station between the line to the
sub-point of the non-GSO space station considered and a point on the GSO arc.
The space station e.i.r.p. mask is defined by the maximum e.i.r.p. radiated in the reference bandwidth
by the space station as a function of this angle, and is given by:
                              𝑁𝑂𝑁 − 𝐺𝑆𝑂_𝑆𝑆_𝑒. 𝑖. 𝑟. 𝑝. (θ) = 𝐺(θ) + 𝑃
where:
NON-GSO_SS_e.i.r.p. :   equivalent isotropic radiated power in the reference bandwidth
                 (dB(W/BWref))
                   angle seen from the non-GSO space station between the line to the sub-point of
                     the non-GSO space station considered and a point on the GSO arc (degrees)
            G():    space station antenna gain pattern (dBi) corresponding to the aggregation of all
                     beams
             P:      maximum power, in the reference bandwidth (dB(W/BWrif))
           BWrif:    reference bandwidth (kHz).


C4      pfd and e.i.r.p. mask format

C4.1    General structure of masks
The pfd and e.i.r.p. masks are key inputs to Recommendation ITU-R S.1503 with format:
–       For epfd(down) runs, the pfd mask(s), containing tables of pfd(, long) or of pfd(azimuth,
        elevation) together with the latitude for which each table is valid.
–       For epfd(up) runs, the non-GSO earth station e.i.r.p. mask(s), each one contains tables of
        e.i.r.p. () or e.i.r.p. (.az, el, long) together with the latitude for which each table is valid.
–       For epfd(IS) runs, the non-GSO satellite e.i.r.p. masks, each one contains tables of e.i.r.p. ()
        together with the latitude for which each table is valid.
During the simulation, the software will calculate the relevant parameters, such as latitude and
off-axis angle or  angle, and then use the mask to calculate a pfd or e.i.r.p. using the following
approach:
1)       The array of {Latitude, Table} is searched and the table which has the nearest latitude to the
         value calculated in the simulation is selected.
2)       Using the selected table, the pfd or e.i.r.p. is then calculated by interpolation using:
        a) pfd: calculated using bi-linear interpolation in either pfd(, long) or pfd(azimuth,
           elevation);
        b) e.i.r.p.(): calculated using linear interpolation in e.i.r.p. ();
        c) e.i.r.p.(az, el, long): using the methodology in § D5.2.7.
```


---

## [Página 39]

```text
                                         Rec. ITU-R S.1503-4                                            37

Each table is independent i.e. at different latitudes it can use a different grid resolution and range. The
mask does not need to cover the whole range: outside the supplied values the last valid value is
assumed to be used.
However, it should be noted that for latitude and {azimuth, elevation, , long} regions where no
actual pfd is produced, in order to avoid using nearest latitude table containing operational pfd values
it is advisable to provide extremely low pfd values for these ranges to simulate no transmission
scenario.
The pfd mask table is not assumed to be symmetric in {azimuth, elevation, , long} and should be
given for the full range from positive to negative extremes. In the case that the {azimuth, elevation,
, long, off-axis angle} calculated in the simulation is outside the ranges given in the pfd or e.i.r.p.
masks, then the last valid value should be used.
For the ES e.i.r.p. masks, there is the option to specify the position by (latitude, longitude) rather than
density via a reference to a specific ES in the SRS. Note that it is not permitted to mix types: either
all non-GSO ES are to be defined via specific ES or all via the density field.
Each mask has header information giving:
–      Notice ID
–      Satellite name
–      Mask ID
–      Lowest frequency mask is valid in MHz
–      Highest frequency mask is valid in MHz
–      Reference bandwidth of mask in kHz
–      Mask type
–      Parameters of mask.
The reference bandwidth of the mask is required as there are more than one bandwidth in the epfd
thresholds in Article 22 of the Radio Regulations. The mask is scaled assuming the pfd or e.i.r.p. has
a constant spectral power density, so that the pfd or e.i.r.p. to use in the calculations is:
                pfd_Calc = pfd_Mask + 10log10[ Threshold_Bandwidth / refbw_khz]
If no refbw_khz is present in the mask than ref_bw_khz=40 is assumed.
            e.i.r.p._Calc = e.i.r.p._Mask + 10log10[ Threshold_Bandwidth / refbw_khz]
The pfd and e.i.r.p. levels can vary by latitude: at least one latitude value (by default, 0 degree) should
be given.
Each XML file should contain one mask (pfd or e.i.r.p.).
The masks relationships are shown in Figs 9 to 12.
```


---

## [Página 40]

```text
38                 Rec. ITU-R S.1503-4

                             FIGURE 9
            Structure of pfd mask data for epfd(down)




                            FIGURE 10
     Structure of off-axis based e.i.r.p. mask data for epfd(up)
```


---

## [Página 41]

```text
                                         Rec. ITU-R S.1503-4                                 39

                                                  FIGURE 11
                      Structure of azimuth elevation based e.i.r.p. mask data for epfd(up)




                                                  FIGURE 12
                                  Structure of e.i.r.p. mask data for epfd(IS)




The pfd masks are to be provided to the ITU BR in XML format as:
–       It is both machine readable and human readable
–       Allows both format and type checking
–       Is an international standard for exchange of data.
The XML format is plain text with opening and closing blocks, as in:
       <satellite_system>
```


---

## [Página 42]

```text
40                                          Rec. ITU-R S.1503-4

        </satellite_system>
Within each section there are then fields relevant to that block. All angles should be given in degrees.
At the top level the satellite system is identified via its notice ID and name using:
        <satellite_system ntc_id="NNNNNNN" sat_name="NAME">
             [Header]
             [Tables]
        </satellite_system>
Within this structure there is the header followed by each of the tables.
The format for each mask is described in more detail in the sections below.

C4.2      pfd mask for epfd(down)
The header format of the pfd mask is as follows:
        <pfd_mask mask_id="N" low_freq_mhz="F1" high_freq_mhz="F2" refbw_khz = “BW”
        type="Type" a_name="latitude" b_name="B" c_name="C">
where (see Table 5):

                                                  TABLE 5
                                           pfd mask header format
            Field                          Type or range            Units               Example
 mask_id                      Integer                                –       3
 low_freq_mhz                 Double precision                      MHz      10 000
 high_freq_mhz                Double precision                      MHz      12 000
 refbw_khz                    Double precision                      kHz      40
 type                         {alpha_deltaLongitude,                 –       alpha_deltaLongitude
                              azimuth_elevation}
 a_name                       {latitude}                             –       latitude
 b_name                       {alpha, azimuth}                       –       alpha
 c_name                       {deltaLongitude, elevation}            –       deltaLongitude


For each of a, b, c there are then arrays of values, such as:
<by_a a="N">
</by_a>
The values within that open/close structure then all relate to a = N: a similar structure is used for
b values.
The innermost group gives the actual pfd value, such as:
        <pfd c="0">–140</pfd>
An example pfd mask would therefore be:
        <satellite_system ntc_id="12345678" sat_name="MySatName">
```


---

## [Página 43]

```text
                         Rec. ITU-R S.1503-4                             41

<pfd_mask mask_id="3" low_freq_mhz="10000" high_freq_mhz="40000" refbw_khz =
“40”      type="alpha_deltaLongitude"   a_name="latitude"     b_name="alpha"
c_name="deltaLongitude">
<by_a a="0">
   <by_b b="–180">
      <pfd c="–20">–150</pfd>
      <pfd c="0">–140</pfd>
      <pfd c="20">–150</pfd>
   </by_b>
   <by_b b="–8">
      <pfd c="–20">–165</pfd>
      <pfd c="0">–155</pfd>
      <pfd c="20">–165</pfd>
   </by_b>
   <by_b b="–4">
      <pfd c="–20">–170</pfd>
      <pfd c="0">–160</pfd>
      <pfd c="20">–170</pfd>
   </by_b>
   <by_b b="0">
      <pfd c="–20">–180</pfd>
      <pfd c="0">–170</pfd>
      <pfd c="20">–180</pfd>
   </by_b>
   <by_b b="4">
      <pfd c="–20">–170</pfd>
      <pfd c="0">–160</pfd>
      <pfd c="20">–170</pfd>
   </by_b>
   <by_b b="8">
      <pfd c="–20">–165</pfd>
      <pfd c="0">–155</pfd>
      <pfd c="20">–165</pfd>
   </by_b>
   <by_b b="180">
      <pfd c="–20">–150</pfd>
```


---

## [Página 44]

```text
42                                        Rec. ITU-R S.1503-4

                 <pfd c="0">–140</pfd>
                 <pfd c="20">–150</pfd>
               </by_b>
        </by_a>
        </pfd_mask>
        </satellite_system>
The XML format can be used to submit a shorthand form to avoid duplicated data but the table will
be completed by interpolation or repeated use of the previous value in an array. The form of the pfd
mask used by the epfd calculation algorithm in Part D is a grid at each latitude of pfd(x, y) where (x,
y) are the two variables of the mask.
At each latitude there will be an array of x and y values:
        xi = {x1, x2, .. xn}
        yi = {y1, y2, .. yn}
These arrays can vary between latitudes.
Then for each (xi, yi) there is an associated pfd value and hence there are n × m pfd values in the table,
as in the simplified 4 × 4 table below:

     (x, y) array                 x1                x2                   x3                   x4
          y1                   pfd(1,1)          pfd(2,1)             pfd(3,1)             pfd(4,1)
          y2                   pfd(1,2)          pfd(2,2)             pfd(3,2)             pfd(4,2)
          y3                   pfd(1,3)          pfd(2,3)             pfd(3,3)             pfd(4,3)
          y4                   pfd(1,4)          pfd(2,4)             pfd(3,4)             pfd(4,4)


The data submitted can be a subset of this, for example:

     (x, y) array                 x1                x2                   x3                   x4
          y1                      –              pfd(2,1)             pfd(3,1)                –
          y2                   pfd(1,2)             –                    –                 pfd(4,2)
          y3                   pfd(1,3)             –                    –                 pfd(4,3)
          y4                      –              pfd(2,4)             pfd(3,4)                –


In this case, the pfd table would be completed by a combination of assumptions. Firstly, if there are
edge of table values not specified, to extend the mask by using the nearest defined value and assuming
it continues:
         pfd(1,1) = pfd(2,1)
         pfd(4,1) = pfd(3,1)
         pfd(1,4) = pfd(2,4)
         pfd(4,4) = pfd(3,4)
Then if there are central table values not specified, to complete using linear interpolation from the
surrounding values as follows:
        pfd(2,2) = Interpolate{x2, pfd(1,2), pfd(4,2), x1, x4}
```


---

## [Página 45]

```text
                                          Rec. ITU-R S.1503-4                                         43

        pfd(2,3) = Interpolate{x3, pfd(1,2), pfd(4,2), x1, x4}
        pfd(3,2) = Interpolate{x2, pfd(1,3), pfd(4,3), x1, x4}
        pfd(3,3) = Interpolate{x3, pfd(1,3), pfd(4,3), x1, x4}
This completion of the pfd mask could be undertaken when it is read in or on-the-fly during the
calculation of the pfd for a specific geometry.

C4.3    e.i.r.p. mask for epfd(up)
The header format of the e.i.r.p.(up) mask is as follows:
        <eirp_mask_es mask_id=“N” low_freq_mhz=“F1” high_freq_mhz=“F2” refbw_khz =
        “BW” a_name=“latitude” b_name = “offaxis angle” ES_ID = “–1”>
where (see Table 6):

                                                TABLE 6
                             Non-GSO ES e.i.r.p. mask header format
           Field                        Type or range            Units             Example
         mask_id           Integer                                 –                   1
       low_freq_mhz        Double precision                       MHz               10 000
       high_freq_mhz       Double precision                       MHz               12 000
        refbw_khz          Double precision                       kHz                  40
          format           Character                               –              “T” or “A”
          a_name           {latitude}                            degrees            latitude
          b_name           {offaxis angle}                       degrees             angle
          ES_ID            Integer                                 –                12345678
                                                                              –1 if not specific ES
          c_name           {azimuth angle}                       degrees               45
          d_name           {elevation angle}                     degrees               15
          e_name           {deltalongES angle}                   degrees               5


If the format =“T” then there are then for each relevant latitude arrays of e.i.r.p. values for given
off-axis angles, such as:
        <eirp b="0">30.0206</eirp>
The e.i.r.p. mask should be monotonically decreasing.
An example e.i.r.p.(up) mask would therefore be:
        <satellite_system ntc_id="12345678" sat_name="MySatName">
        <eirp_mask_es mask_id="1" low_freq_mhz="10000" high_freq_mhz="40000" refbw_khz =
        “40” format = "T" a_name = "latitude" b_name="offaxis angle", ES_ID=–1>
        <by_a a="0">
        <eirp b="0">30.0206</eirp>
        <eirp b="1">20.0206</eirp>
        <eirp b="2">12.49485</eirp>
```


---

## [Página 46]

```text
44                                     Rec. ITU-R S.1503-4

       <eirp b="3">8.092568</eirp>
       <eirp b="4">4.9691</eirp>
       <eirp b="5">2.54634976</eirp>
       <eirp b="10">–4.9794</eirp>
       <eirp b="15">–9.381681</eirp>
       <eirp b="20">–12.50515</eirp>
       <eirp b="30">–16.90743</eirp>
       <eirp b="50">–18.9471149</eirp>
       <eirp b="180">–18.9471149</eirp>
       </by_a>
       </eirp_mask_es>
       </satellite_system>
If the format = “A” then at each latitude there would be a set of tables for every (azimuth, elevation)
pointing angle covered, with each table of e.i.r.p. against the difference in longitude between the ES
and the point on the GSO arc.
An example e.i.r.p.(up) mask would therefore be:
       <satellite_system ntc_id="12345678" sat_name="MySatName">
       <eirp_mask_es mask_id="1" low_freq_mhz="10000" high_freq_mhz="40000" refbw_khz =
       "40" format = "A" a_name = "latitude" c_name="azimuth angle" d_name = "elevation
       angle" e_name = "DeltaLongES", ES_ID=–1>
       <by_a a="0">
       <by_c c = “0”>
       <by_d d= “0”>
       <eirp e="0">30.0206</eirp>
       <eirp e="1">20.0206</eirp>
       <eirp e="2">12.49485</eirp>
       <eirp e="3">8.092568</eirp>
       <eirp e="4">4.9691</eirp>
       <eirp e="5">2.54634976</eirp>
       <eirp e="10">–4.9794</eirp>
       <eirp e="15">–9.381681</eirp>
       <eirp e="20">–12.50515</eirp>
       <eirp e="30">–16.90743</eirp>
       <eirp e="50">–18.9471149</eirp>
       <eirp e="180">–18.9471149</eirp>
       </by_d>
```


---

## [Página 47]

```text
                                           Rec. ITU-R S.1503-4                                            45

C4.4    e.i.r.p. mask for epfd(IS)
The header format of the e.i.r.p.(IS) mask is as follows:
        <eirp_mask_ss mask_id="N" low_freq_mhz="F1" high_freq_mhz="F2" refbw_khz = “BW”
        a_name= “latitude” b_name="offaxis angle">
where (see Table 7):
                                                 TABLE 7
                           Non-GSO satellite e.i.r.p. mask header format
           Field                        Type or range              Units               Example
         mask_id              Integer                                 –                    1
       low_freq_mhz           Double precision                      MHz                 10 000
       high_freq_mhz          Double precision                      MHz                 12 000
        refbw_khz             Double precision                      kHz                   40
          a_name              {latitude}                              –                 latitude
          b_name              {angle}                                 –                  angle


There are then for each relevant latitude arrays of e.i.r.p. values for given off-axis angles, such as:
        <eirp b="0">30.0206</eirp>
The e.i.r.p. mask should be monotonically decreasing.
An example e.i.r.p.(IS) mask would therefore be:
        <satellite_system ntc_id="12345678" sat_name="MySatName">
        <eirp_mask_ss mask_id="2" low_freq_mhz="10000" high_freq_mhz="40000" refbw_khz =
        “40” a_name = “latitude” b_name="offaxis angle">
        <by_a a="0">
        <eirp b="0">30.0206</eirp>
        <eirp b="1">20.0206</eirp>
        <eirp b="2">12.49485</eirp>
        <eirp b="3">8.092568</eirp>
        <eirp b="4">4.9691</eirp>
        <eirp b="5">2.54634976</eirp>
        <eirp b="10">–4.9794</eirp>
        <eirp b="15">–9.381681</eirp>
        <eirp b="20">–12.50515</eirp>
        <eirp b="30">–16.90743</eirp>
        <eirp b="50">–18.9471149</eirp>
        <eirp b="180">–18.9471149</eirp>
        </by_a>
        </eirp_mask_ss>
        </satellite_system>
```


---

## [Página 48]

```text
46                                      Rec. ITU-R S.1503-4

                                               PART D

                         Software for the examination of non-GSO filings


D1      Introduction

D1.1    Scope
The scope of this section is to specify part of a software requirements document (SRD) for a computer
program that can be used by the BR to calculate whether a specific non-GSO system proposed by an
administration meets epfd limits.
There are three key tasks that the software must complete as identified in Fig. 1:
1)      determination of the runs to execute;
2)      for each run, determination of worst case geometry;
3)      for each run, calculation of epfd statistics and checking compliance with the limits.

D1.2    Background
This section assumes that the following approaches are used:
epfd calculation: Each non-GSO satellite has a pfd mask and the pfd for each satellite is used to
calculate the aggregate epfd↓ at an earth station of a GSO system. This is repeated for a series of time
steps until a distribution of epfd↓ is produced. This distribution can then be compared with the limits
to give a go/no go decision.
epfd calculation: The Earth is populated with a distribution of non-GSO earth stations. Each earth
station points towards a non-GSO satellite using pointing rules for that constellation, and transmits
with a defined e.i.r.p.. From the e.i.r.p. mask for each earth station, the epfd↑ at the GSO can be
calculated. This is repeated for a series of time steps until a distribution of epfd↑ is produced. This
distribution can then be compared with the limits to give a go/no go decision.
epfdis calculation: From the e.i.r.p. mask for each space station, the epfdis at the GSO space station
can be calculated. This is repeated for a series of time steps a distribution of epfdis is produced. This
distribution can then be compared with the limits to give a go/no go decision.
The SRD provides detailed algorithms that would allow it to be implemented in software by any
interested parties without reference to any specific development methodology.

D1.3    Overview
This Part D is structured into the following sections:
Section D2:       Determination of runs to execute
Section D3:       Determination of worst case geometry for each run
Section D4:       Calculation of time step size and number of time steps
Section D5:       Calculation of epfd statistics and limit compliance checking
Section D5.1: Defines the epfd↓ algorithm
Section D5.2: Defines the epfd↑ algorithm
Section D5.3: Defines the epfdis algorithm
Section D6:       Defines the core geometry and algorithms used by both epfd calculations including
                  gain patterns
```


---

## [Página 49]

```text
                                         Rec. ITU-R S.1503-4                                         47

Section D7:       Specifies the output formats and process to obtain a go/no-go decision.
Note that where square brackets are included as part of a parameter name, this indicates an index into
an array, not tentative text.

D1.4       General assumptions and limitations
A general limitation on the generation of epfd statistics is:
        Bin size: SB = 0.1 dB
To be consistent with the evaluation algorithm in § D7.1.3 epfd values calculation for each time step
should be rounded to the lower values with a maximum precision of 0.1 dB.
The calculation of angle to GSO arc, , as described in § D6.4.4 should, where feasible, be calculated
using the analytic method. If this method is unable to provide a solution, then the iterative approach
may be used based upon a number of test points, with specified separation between them:
        Separation between GSO test points: 1e-6 radians.
The test points should be a located at integer multiples of 1e-6 radians.

D1.5       Database and interface
Automatic verification analysis should take input data from the SRS or other databases, combined
with BR resources such as DLLs to define epfd limits and calculate antenna gains. Custom analysis
can request some parameters, such as GSO satellite and ES locations, from the user.


D2         Determination of runs to execute

D2.1       Article 22 runs
For an Article 22 run a key task is to determine which runs to execute given a non-GSO filing and
the epfd limits specified in the RR.
In any direction, if there are no masks, whether pfd or e.i.r.p., then it is not necessary to undertake
any runs.
It is necessary to look in the SRS grp, freq tables to:
–        Identify the date involved
–        Identify the list of frequencies.
It is also necessary to check the frequencies of the system operating table: if there are different sets
of parameters at different frequencies then a run will have to be executed for each unique set of
pfd/e.i.r.p. mask, orbit elements and system operating characteristics.
For each {freq_min, freq_max, date} combination is used to call the LimitsAPI. If there are duplicate
limits returned, then only the minimum frequency case need be run.

For all unique Satellite {freq_min, freq_max, date} in non-GSO notice
{
     From LimitsAPI request all FSS epfd(down) limits for {freq_min, freq_max,
date}
     For all unique epfd(down) limits returned

       {
           Set FrequencyRun = max(fmin(mask), fmin(limits)) + RefBW/2
           CreateRun:
```


---

## [Página 50]

```text
48                                Rec. ITU-R S.1503-4

            Direction = Down
            Service = FSS
            Frequency = FrequencyRun
            ES_DishSize = From Limits API
            ES_GainPattern = From Limits API
            epfd_Threshold = From Limits API
            Ref_BW = From Limits API
        }
     From LimitsAPI request all BSS epfd(down) limits for {freq_min, freq_max,
date}
     For all unique epfd(down) limits returned
     {

         Set FrequencyRun = max(fmin(mask), fmin(limits)) + RefBW/2
         CreateRun:
            Direction = Down
            Service = BSS
            Frequency = FrequencyRun
            ES_DishSize = From Limits API
            ES_GainPattern = From Limits API
            epfd_Threshold = From Limits API
            Ref_BW = From Limits API

     }
}
For all unique ES {freq_min, freq_max} in non-GSO notice
{
     From LimitsAPI request all epfd(up) limits for {freq_min, freq_max, date}
     For all unique epfd(up) limits returned
     {
       Set FrequencyRun = max(fmin(mask), fmin(limits)) + RefBW/2
       CreateRun:
          Direction = Up
          Frequency = FrequencyRun
          Sat_Beamwidth = From Limits API
          Sat_GainPattern = From Limits API
          epfd_Threshold = From Limits API
          Ref_BW = From Limits API
     }
}
For all unique Satellite {freq_min, freq_max, date} in non-GSO notice
{
     From LimitsAPI request all epfd(is) limits for {freq_min, freq_max, date}
     For all unique epfd(is) limits returned
     {
       Set FrequencyRun = max(fmin(mask), fmin(limits)) + RefBW/2
       CreateRun:
          Direction = Intersatellite
          Frequency = FrequencyRun
          Sat_Beamwidth = From Limits API
          Sat_GainPattern = From Limits API
          epfd_Threshold = From Limits API
          Ref_BW = From Limits API
```


---

## [Página 51]

```text
                                     Rec. ITU-R S.1503-4                                       49

       }
}


D2.2       Number 9.7A of the Radio Regulations
For RR No. 9.7A runs the criteria and threshold is defined in RR Appendix 5 and runs are generated
as follows:

If the selected earth station meets the criteria in Appendix 5
{
     Get the frequency range of the selected ES(fmin, fmax)
     Get all non-GSO networks in the SRS that overlap that frequency range
     For each non-GSO network returned
     {
          Query Limits API with the selected ES(fmin, fmax)
          {
              Get RefBW from Appendix 5 Data
              Set FrequencyRun = max(ES_fmin, Mask_fmin) + RefBW/2
              CreateRun:
               Direction = Down
               Frequency = FrequencyRun
               ES_DishSize = From ES filing
               ES_GainPattern = From ES filing
               epfd_Threshold = From Appendix 5
               Ref_BW = From Appendix 5
          }
     }
}


D2.3       Number 9.7B of the Radio Regulations
For RR No. 9.7B runs the criteria and threshold is defined in RR Appendix 5 and runs are generated
as follows:

Get (fmin, fmax) from non-GSO notice
{
     Get all ES in the SRS that overlap that frequency range
     For each ES returned
     {
       If the earth station meets the criteria in Appendix 5
       {
           Query Limits API with ES(fmin, fmax)
           Get RefBW from Appendix 5 Data
           Set FrequencyRun = max(ES_fmin, Mask_fmin) + RefBW/2
           CreateRun:
               Direction = Down
               Frequency = FrequencyRun
               ES_DishSize = From ES filing
               ES_GainPattern = From ES filing
               epfd_Threshold = From Appendix 5
               Ref_BW = From Appendix 5
           }
       }
```


---

## [Página 52]

```text
50                                        Rec. ITU-R S.1503-4

      }
}


D3        Worst-case geometry
The epfd limits in RR Article 22 are applicable for all GSO ESs and all pointing angles towards that
part of the GSO arc visible from that ES. It is, however, not feasible to model all such geometries
within the verification software. The worst-case geometry (WCG) is a reference GSO satellite
location and either an ES or boresight of the GSO satellite’s beam which is used when examining a
non-GSO system for compliance with the epfd limits in RR Article 22. It remains necessary for the
non-GSO operator to meet the epfd limits in RR Article 22 for all other geometries including the
testing of specific GSO networks as noted in § A1.3.
The WCG is selected by an algorithm, the worst-case geometry algorithm (WCGA), which
undertakes an examination of the pfd/e.i.r.p. masks together with the non-GSO satellite orbital
parameters to identify the highest single entry epfd value. Where there are multiple geometries with
the same highest single entry epfd value, then the geometry is selected that should have this highest
single entry epfd for the greatest percentage of time, identified by considering the angular velocity or
elevation angle. These assumptions are based on the critical epfd levels being the highest ones which
are those that are most readily measurable.
The WCGA is based upon iterating over a set of positions, typically of the non-GSO satellite. The
geometry is assumed to be symmetric in longitude and the satellite can be set at the required latitude
exactly using a simple point-mass model. However the epfd calculation engine described in § D5 can
use a range of orbit models with a specific time step and therefore the longitude at which a satellite
reaches a specified latitude will be different from that in the WCGA. Hence an additional stage is
required which for the non-GSO satellite that results in the highest single entry epfd the difference in
longitude is calculated between:
–       The longitude of the non-GSO satellite when it reaches the specified latitude using a point
        mass model in the WCGA, converting to (latitude, longitude) using a static time
        t = simulation start time (e.g. relative time = 0).
–       The longitude at which the non-GSO satellite is closest to the specified latitude using the full
        orbit model and the fine time step calculated for the given run, converting to (latitude,
        longitude) using the relevant simulation time.
This difference in longitude is shown in Fig. 13.

                                                   FIGURE 13
                  Adjusting longitude of WCG to take account of orbit model and simulation time
```


---

## [Página 53]

```text
                                         Rec. ITU-R S.1503-4                                            51

This difference in longitude is then used to adjust the position of the GSO satellite and ES calculated
in the WCGA so that the non-GSO satellite with the pfd mask that causes the highest epfd goes
through the geometry that causes this epfd value during its first orbit. When iterating for latitude using
the time step, the time step that gives the nearest latitude is used in the longitude calculation.
Note that the algorithm in this Recommendation is not designed to take account of either ITU-R
Regions or specific longitudes as the epfd limits in RR Article 22 are meant to be applicable for all
GSO ES locations and visible parts of the GSO arc.
Care is required when comparing floating point numbers to check for rounding errors. In the WCGA
it is acceptable to round to the nearest 0.1 dB rather than round up. The search grid is in steps of 0.1°
and binary search routines terminate when the difference in angles is less than 1e-5 radians.

D3.1    WCG epfd↓
D3.1.1 Inputs
The inputs to the algorithm include:
       pfd_mask: the pfd mask to check
          0[lat] :    the GSO arc avoidance angle of the non-GSO satellite by latitude =
                       MIN_EXCLUDE[Latitude]
                h:     the minimum operating height of the non-GSO satellite
       0[lat, az] :   the minimum elevation angle of the non-GSO satellite by latitude and azimuth
         {a, e, i}:    the orbit parameters of the non-GSO system
                ES:    the parameters of the ES including gain pattern.
D3.1.2 Algorithm
This section describes the algorithm to determine the WCG for the epfd(down) direction.
Note that there could be different ranges of frequency in the pfd masks: this process is assumed to be
repeated for each valid frequency range. For each valid frequency ranges there could be different pfd
masks, multiple sets of {a, e, i} or system operating parameters (e.g. GSO avoidance angles that vary
by non-GSO satellite): the process should be repeated over each such set.
The WCG is based upon search in (, φ) as seen by the non-GSO satellite, with particular care taken
for the region (–0, +0) including  = 0. This search is repeated at a number of test non-GSO satellite
latitudes. In addition, specific checks are made for the highest latitudes for which  = {−0, 0, +0}
to ensure compatibility with the methodology in Recommendation ITU-R S.1714.
For each test point considered, the algorithm calculates the epfd using the pfd mask and receive
antenna gain, and compares that to the threshold for the relevant latitude. The gain is calculated using
the  angle for the off-axis angle : for the BSS ES antenna gain pattern, which might not be
symmetric around the boresight, the  should be the value calculated assuming the ES points at the
location at the point correspond to the  angle. Note that the algorithm can be implemented in a way
that calculates the WCG for multiple dish sizes using vectorisation.
It is likely that multiple test points will result in the same difference between epfd level and threshold.
To assess which should be used as the WCG, the angular velocity of the non-GSO satellite as seen
by the ES is calculated, and the geometry selected is that:
1.        Gives the highest difference between epfd level and threshold to the resolution of the
          resulting statistics (0.1 dB).
```


---

## [Página 54]

```text
52                                    Rec. ITU-R S.1503-4

2.      If multiple geometries meet point 1, select the one that would result in geometry that gives
        the lowest angular velocity of the satellite as seen by the ES.
The search algorithm is shown in Figs 14 and 15, where:
                                         Δα = α − α0 [𝑙𝑎𝑡]
                                       Δε = ε − ε0 [𝑙𝑎𝑡, 𝑎𝑧]

                                              FIGURE 14
                                 Search (, φ) grid for WCGD_CalcAtLat
```


---

## [Página 55]

```text
                                     Rec. ITU-R S.1503-4                             53

                                            FIGURE 15
                                Geometry for WCGD_CheckExtremeCase




The algorithm is described in the following pseudo code for specified GSO ES type:

WCGA_Down:
     Set WorstEPFDBin = –9999
     Set WorstAngularVelocity = +9999
     Identify 0,min = minimum value over all values in the 0[lat, az] table
     For all satellites in the order listed in ITU DB
     {
       Determine PFD mask to use for this satellite
       If this PFD mask and satellite orbit (a, e, i) combination has not been
checked so far then or this satellite uses a different 0[lat] then
          Call GetWCGA_Down(SystemParams)
       End if
     }
     Next satellite

Note that SystemParams = (PFD_Mask, 0[lat], 0[lat, az], ES, OrbitParams, 0,min)
as required

GetWCGA_Down (SystemParams):
     StepSize = 0.1°
     If (i = 0)
     {
       WCGD_CalcAtLat(SystemParams, latitude = 0)
     }
```


---

## [Página 56]

```text
54                                Rec. ITU-R S.1503-4

     Else
     {
       LatNumSteps = RoundUp(i / StepSize)
       For n = 0 to LatNumSteps inclusive
       {
          latitude = i * n / LatNumSteps
          WCGD_CalcAtLat(SystemParams, latitude)
          If (n > 0)
          {
              WCGD_CalcAtLat(SystemParams, -latitude)
          }
       }
         WCGD_CheckExtremeCase(SystemParams, 0,  = +/2}
         WCGD_CheckExtremeCase(SystemParams, 0,  = -/2}
         WCGD_CheckExtremeCase(SystemParams, +1,  = +/2}
         WCGD_CheckExtremeCase(SystemParams, -1,  = –/2}
         WCGD_CheckExtremeCase(SystemParams, +1,  = +/2}
         WCGD_CheckExtremeCase(SystemParams, -1,  = -/2}
     }
}

WCGD_CalcAtLat(SystemParams, latitude):
     Locate non-GSO satellite at latitude
     Calculate height of non-GSO satellite from its radius, r
     If height of non-GSO satellite < minimum operating height           then   return
     Calculate φ0 for elevation angle 0, min and radius r
     WCGD_CheckCase(SystemParams, latitude,  = 0, φ = 0)
     NumPhiSteps = RoundUp(φ0 / StepSize)
     PhiStepSize = φ0 / NumPhiSteps
     For φ = PhiStepSize to φ0 inclusive in NumPhiSteps steps
     {
         ThetaMin = −/2
       ThetaMax = +3/2
       If the PFD mask is symmetric in DeltaLong or Azimuth and elevation table
is also symmetric between east and west
       {
             ThetaMax = /2
         }
         NumThetaSteps = max(16, RoundUp(2φ/PhiStepSize))
         ThetaStepSize = (ThetaMax-ThetaMin)/NumThetaSteps
         For ThetaStep = 0 to NumThetaSteps inclusive
         {
              = ThetaMin + ThetaStep*ThetaStepSize
             WCGD_CheckCase(SystemParams, latitude, , φ)
         }
         WCGD_CheckAlphaPhiCase(SystemParams, latitude, φ, 0, RHS)
         WCGD_CheckAlphaPhiCase(SystemParams, latitude, φ, +1, RHS)
         WCGD_CheckAlphaPhiCase(SystemParams, latitude, φ, -1, RHS)
         If the PFD masks is not symmetric then
         {
            WCGD_CheckAlphaPhiCase(SystemParams, latitude, φ, 0, LHS)
            WCGD_CheckAlphaPhiCase(SystemParams, latitude, φ, +1, LHS)
```


---

## [Página 57]

```text
                                  Rec. ITU-R S.1503-4                              55

            WCGD_CheckAlphaPhiCase(SystemParams, latitude, φ, -1, LHS)
       }
     }
     WCGD_CheckAlphaElevCase(SystemParams, latitude, 0, RHS)
     WCGD_CheckAlphaElevCase(SystemParams, latitude, +1, RHS)
     WCGD_CheckAlphaElevCase(SystemParams, latitude, -1, RHS)
     If the PFD mask is not symmetric then
     {
       WCGD_CheckAlphaElevCase(SystemParams, latitude, 0, LHS)
       WCGD_CheckAlphaElevCase(SystemParams, latitude, +1, LHS)
       WCGD_CheckAlphaElevCase(SystemParams, latitude, -1, LHS)
     }

WCGD_CheckCase(SystemParams, latitude, , φ):
     Convert (,φ) to (az, el) in the satellite reference frame
     Create line from non-GSO satellite N in direction (az, el)
     Identify point P in which line intersects Earth
     Calculate the latitude of P, latP
     If Absolute(latP) > 81.2 degrees then exit this function
     If latP < ES_LAT_MIN then exit this function
     If latP > ES_LAT_MAX then exit this function
     If number of non-GSO satellites that operate at this latitude is zero then
exit this function
     Calculate the (aznGSO, elnGSO) of the non-GSO satellite as seen by the ES
     Find the nearest latitude to latP in the 0[latP, AznGSO] table
     At point P calculate (, long) angles wrt point N
     At point P calculate AngularVelocity using methodology below
     Calculate PFD from mask, latitude & (az, el, , long)
     Calculate G() and G(0[latP])
     Calculate the elGSO of the point on the GSO arc associated with the calculated

GSO = the appropriate minimum elevation angle in Table 8
If ((  0[latP] and elnGSO  0[latP, AznGSO] and elGSO >= εGSO)
         or G() > min(Gmax -30 dB, G(0[latP])) ) then
     {
         Calculate EPFDThreshold from latitude of point P on the Earth’s surface
         Calculate EPFDMargin = PFD + Grel() - EPFDThreshold
         Calculate EPFDbin = EPFDMargin/BinSize
         If WorstEPFDBin < EPFDBin
         {
            WorstEPFDBin = EPFDBin
            Worst AngularVelocity = AngularVelocity
            Store this (N, P)
         }
         Else if (WorstEPFDBin = EPFDBin &&
                WorstAngularVelocity > AngularVelocity)
         {
            WorstAngularVelocity = AngularVelocity
            Store this (N, P)
         }
     }
```


---

## [Página 58]

```text
56                                Rec. ITU-R S.1503-4

WCGD_CheckAlphaPhiCase(SystemParams, Latitude, φ, Sign, Side):
     Set  range according to side to check (left or right)
     If bracket  = 0 then
     {
       Use binary search to iterate on  until WCGD_GetDeltaAlpha(SystemParams,
Sign, , φ) = 0
         WCGD_CheckCase(SystemParams, latitude, , φ)
     }



WCGD_CheckAlphaElevCase(SystemParams, Latitude, Sign, Side):
     Set  range according to side to check (left or right)
     If bracket  = 0 then
     {
         Use binary search to iterate on  until within 1e-5 radians
         {
             For each i
             {
                 Call WCGD_CalcPhiFromThetaElev(SystemParams, i) to determine φi
                 Call WCGD_GetDeltaAlpha(SystemParams, Sign, i φi) to determine i
             }
         } selecting  that brackets  = 0
         WCGD_CheckCase(SystemParams, latitude, , φ)
     }

WCGD_CalcPhiFromThetaElev(SystemParams, ThetaTest, PhiMax):
     φ0 = 0
     φ1 = PhiMax
     Use binary search to iterate on φ until within 1e-5 radians
     {
       For each φi
       {
          Call WCGD_CalcDeltaElev(SystemParams, ThetaTest, φi)

         }
     } selecting φ that brackets  = 0
     Return φ

WCGD_GetDeltaAlpha(SystemParams, Sign, , φ):
     Convert (,φ) to (az, el)
     Create line in direction (az, el) from non-GSO satellite
     Identify point P where line intersects Earth
     Calculate latitude of P, latP
     Determine exclusion zone size at this point, 0[latP]
     At point P calculate 
     deltaA = Sign*0[latP]
     Return  - deltaA

WCGD_GetDeltaElev(SystemParams, ,φ):
     Convert (,φ) to (az, el)
     Create line in direction (az, el) from non-GSO satellite
```


---

## [Página 59]

```text
                                         Rec. ITU-R S.1503-4                                            57

      Identify point P where line intersects Earth
      Calculate latitude of P, latP
      Calculate (aznGSO, elnGSO) of non-GSO satellite as seen by point P
      At point P calculate 0[lat, az]
      Return elnGSO - 0[lat, az]

WCGD_CheckExtremeCase(SystemParams, Sign, ):
     Set latitude range according to sign (north or south hemisphere)
      If bracket  = 0 then
      {
        Use binary search to iterate until latitude range less than 1e-5 radians
        {
           For each test latitude, Lat
           {
             Call WCGD_CalcDeltaAlphaFromLatElev(SystemParams, Lat, Sign, i) to
determine i and corresponding φi
          }
          } Selecting latitudes that bracket  = 0
          WCGD_CheckCase(SystemParams, latitude, , φ)
      }

WCGD_CalcDeltaAlphaFromLatElev(SystemParams, Latitude, Sign, ):
     Set satellite at Latitude
      Get φ using WCGD_CalcPhiFromThetaElev SystemParams, , PhiMax)
      Calculate  from WCGD_CalcDeltaAlpha(SystemParams, Sign, , φ)
      Return , φ


The algorithm uses the geometry given in the sections below.
D3.1.3 Geometry

D3.1.3.1 Conversion between (az, el) and (, φ)
The following equations can be used:
             cos(φ) = cos(𝑎𝑧) cos(𝑒𝑙)
             sin(𝑒𝑙) = sin(θ) sin(φ)
Note that it is necessary to check the sign of the az or φ to ensure that the arccos / arcsin is calculated
correctly.
It should also be noted that the equations above should be used in the application of the worst-case
geometry algorithm and that they are different from the equations converting (θ, ) into (Az, El)
included in § C2.3.2.
The (, φ) coordinate system and its relationship to the (az, el) coordinate system is shown in Figs 16
and 17 below.
```


---

## [Página 60]

```text
58                                         Rec. ITU-R S.1503-4

                                                   FIGURE 16
                    Conversion between (Azimuth, Elevation) and (, φ) from satellite viewpoint




                                                   FIGURE 17
                            Conversion between (Azimuth, Elevation) and (, φ) in 3D




The φ angle is between the two lines:
–      from the satellite to the sub-satellite point
```


---

## [Página 61]

```text
                                          Rec. ITU-R S.1503-4                                       59

–        from the satellite to the test direction.
The  angle is around the boresight in an anti-clockwise direction from the elevation = 0 plane.
D3.1.3.2 Setting satellite at latitude
Key steps in this algorithm are the calculation of the position and velocity vectors of the non-GSO
satellite and ES. For circular orbit systems the latitude can be used to derive the true anomaly, ,
using:
                                                                   sin 𝑙𝑎𝑡
                                           sin(ω + ) =             sin 𝑖

For elliptical systems, it is necessary to use binary search. Assuming the system is designed to have
with argument of perigee = ±/2, then the satellite will go from minimum to maximum latitude when
the mean anomaly varies from [0, ]. Therefore, the binary search can start with M = (0, ) and iterate
from there.
To derive the position and velocity vectors, the following equations could be used:
In the satellite plane:
                                     𝑟𝑠𝑎𝑡 = 𝑟𝑠𝑎𝑡 (cos  𝑃 + sin  𝑄)
                                            𝜇
                                 𝑠𝑎𝑡 = √𝑝 (– sin  𝑃 + (𝑒 + cos ) 𝑄)

where:
             P, Q:    unit vectors in the orbit plane with origin the centre of the Earth and P aligned
                      with the orbit’s major axis as described in § D6.3.3
           a, e, :   orbital elements.
Also:
                                                               𝑝
                                                𝑟𝑠𝑎𝑡 = 1+𝑒 cos 
                                                𝑝 = 𝑎(1 − 𝑒 2 )
The non-GSO’s position and velocity vectors can then be converted from the PQW orbit plane based
frame to Earth centred vectors using the standard rotation matrix via the (, , i) orbital elements.
For the WCG calculation it can be assumed that second order effects including the J2 factor need not
be considered.
The position vector equation can also be used to calculate a latitude from a true anomaly, , and hence
by iteration locate the satellite at the required latitude.
D3.1.3.3 Calculation of maximum φ in satellite viewpoint
For a given latitude and hence satellite radius, the maximum angle at the satellite from sub-satellite
point φ0 can be derived from the elevation angle  using:
                                                       𝑅             π
                                        sin(φ0 ) = 𝑟 𝑒 sin ( 2 + ε)
                                                       𝑠𝑎𝑡


D3.1.3.4 Calculation of angular velocity
The inputs are as follows:
       ES position vector:                              res
         ES velocity vector:                            es
         Non-GSO satellite position vector:             rsat
```


---

## [Página 62]

```text
60                                        Rec. ITU-R S.1503-4

        Non-GSO satellite velocity vector:               sat
From these, it is possible to calculate the apparent velocity and vector from the ES to the satellite:
                                                 r = rsat – res
                                                  = sat – es
The angle between these two vectors can then be calculated:
                                                                𝑟∙
                                                 cos ψ = 𝑟
The instantaneous angular velocity is then:
                                                       
                                                 θ = 𝑟 sin ψ
The various terms are shown in Fig. 18.

                                                   FIGURE 18
                         Vectors to calculate non-GSO satellite apparent angular velocity




Note that a low angular velocity will result in higher likelihoods of interference and hence for a given
epfd value the WCG that gives the least apparent angular velocity would be the one to use.
The ES’s velocity vector can be derived from its position vector (x, y, z) as follows:
                                                       –𝑦
                                           𝑒𝑠 = 𝑤𝑒 ( 𝑥 )
                                                        0
where we is the Earth’s angular velocity in radians per second.

D3.2    WCG epfd↑
Note that as the epfd limits in Article 22 are for 100% of the time there is no need to consider the
likelihood of particular geometries, only the maximum epfd(up) value.
```


---

## [Página 63]

```text
                                          Rec. ITU-R S.1503-4                                          61

If there are multiple sub-constellations with alternative orbital elements or some satellites use
different exclusion zone angles, then the process should be repeated for each unique set.
D3.2.1 Inputs
The inputs to the algorithm are as follows:
      ES_e.i.r.p.: the non-GSO ES e.i.r.p. mask to check. This could vary by latitude, with each
                     latitude range having an e.i.r.p. pattern, defined as a table of e.i.r.p. vs off-axis
                     angle towards the GSO arc or by latitude, azimuth and elevation, with each
                     latitude/azimuth/elevation having an e.i.r.p pattern defined as a table of e.i.r.p.
                     against difference in longitude between the ES and GSO satellite
             θadB : the GSO satellite’s beam half power beamwidth
           0,GSO :    the minimum elevation angle of the GSO system
       0[lat, az] :   the minimum elevation angle of the non-GSO system, which could vary by
                       latitude and azimuth
          0[lat] :the minimum exclusion angle of the non-GSO system, which could vary by
                   latitude and non-GSO satellite
        Nco[lat] : the number of non-GSO satellites that can provide co-frequency service at a
                   specific location on the Earth’s surface, which could vary by latitude
ES_DISTANCE, ES_DENSITY : the average distance on the Earth’s surface between
                   co-frequency beams from the non-GSO system (km) and density of co-frequency
                   non-GSO ES or:
   ES(lat, long) : the latitude and longitude of the specific ES of the non-GSO system
  {a,i,e, ,,} :     the orbit parameters of the non-GSO system including whether the system’s
                       ground track repeats and if so the repeat time.
D3.2.2 GSO system parameters
It is assumed that the GSO system’s minimum operating elevation angle and beamwidth are as in
Table 8.

                                               TABLE 8
                                   WCG(up) GSO system parameters
              Frequency band                    f < 10 GHz          10 GHz ≤ f <         f  17 GHz
                                                                      17 GHz
 Beamwidth (degrees)                                1.5                   4                  1.55
 Minimum elevation angle (degrees) EOC              10                  10                   20


Table 9 shows the geocentric angle that corresponds to the minimum elevation angle and hence the
maximum angle at the GSO satellite towards the beam boresight φBS:

                                               TABLE 9
                         WCG(up) derived minimum and maximum values
              Frequency band                     f < 10 GHz          10 GHz ≤ f <         f  17 GHz
                                                                       17 GHz
 φEOC at GSO satellite (degrees)                    8.567                8.567               8.172
```


---

## [Página 64]

```text
62                                         Rec. ITU-R S.1503-4


              Frequency band                       f < 10 GHz                10 GHz ≤ f <   f  17 GHz
                                                                               17 GHz
 φBS at GSO satellite (degrees)                        7.817                    6.567         7.397
 Geocentric angle  for φBS (degrees)                  56.230                  42.552         50.934
These were generated using the geometry in Fig. 19 and the following equations.

                                                 FIGURE 19
                                  GSO pointing angles for WCGA(UP) geometry




Here the φBS can be derived as follows:
                                                       𝑅          π
                                        sinφ𝐸𝑂𝐶 = 𝑅 𝑒 sin ( 2 + ε)
                                                       𝑔𝑒𝑜

                                                                θ3𝑑𝐵
                                            φ𝐵𝑆 = φ𝐸𝑂𝐶 –          2

Then, noting that as  > /2:
                                                        𝑅𝑔𝑒𝑜
                                        sin(π – ψ) =            sin (φ𝐵𝑆 )
                                                           𝑅𝑒

Hence:
                                            χ𝐵𝑆 = π − φ𝐵𝑆 − ψ
D3.2.3 Algorithm
The WCGA for the epfd(up) case is as follows:

      WCGA_UP:
         Calculate φBS from EOC
         From φBS calculate BS
         If ES from density
            Call WCGA_UP_General
         Else
            If non-GSO satellite repeats
                Call WCGA_UP_SpecifcES_Repeating
            Else
                Call WCGA_UP_SpecifcES_NonRepeating
```


---

## [Página 65]

```text
                                             Rec. ITU-R S.1503-4                                        63

           Endif
        Endif


The various cases, their functions and geometries are described in the following sections.
D3.2.3.1 Aggregate epfd calculation
In the general case the aggregate epfd(up) can be calculated using:
                 𝑖=𝑁𝐸𝑆

  𝑒𝑝𝑓𝑑(𝑢𝑝) = ∑ 𝑒. 𝑖. 𝑟. 𝑝. (𝑙𝑎𝑡) − 𝐿𝑆 + 𝐺𝑟𝑒𝑙,𝑟𝑥 + 10 log10 (𝑁𝑈𝑀𝐸𝑆 ) + 10 log10 (𝑁𝑐𝑜,𝐸𝑆 (𝑙𝑎𝑡))
                   𝑖=1

Note the summation is in absolute though the equation is given in dB terms with addition and where:
    e.i.r.p. (lat) :     e.i.r.p. towards the GSO for the given non-GSO ES latitude either as a function
                         of latitude and off-axis angle towards the GSO arc or function of latitude,
                         boresight (azimuth, elevation) and difference in longitude between the non-GSO
                         ES and GSO satellite
             LS :        spreading factor
        Grel,rx :        relative gain at the GSO satellite using the Rec. ITU-R S.672 gain pattern
       NUM_ES :          is a factor for systems using density rather than specific ES (and which typically
                         relates to the access method) derived from the non-GSO system’s density and
                         distance fields as given in § D5.2.5
                   NUM_ES = ES_DISTANCE * ES_DISTANCE * ES_DENSITY
      Nco,ES(lat) : maximum number of co-frequency non-GSO ES that can transmit at a given
                     location.
The summation is over the NES non-GSO deployed within the GSO satellites 15 dB beamwidth
footprint using the algorithm in § D5.2.5. This depends upon the separation distance between
co-frequency beams which is a parameter provided by the non-GSO system.
For the WCG(up) calculation, it is assumed that the aggregate epfd is dominated by the single entry
epfd at the boresight plus an aggregation factor and so this can be estimated by extracting the
non-GSO parameters from the summation as in:
                                                                                     𝑖=𝑁𝐸𝑆

    𝑒𝑝𝑓𝑑(𝑢𝑝) ≅ 𝑒. 𝑖. 𝑟. 𝑝. (lat) + 10 log10 (𝑁𝑈𝑀𝐸𝑆 ) + +10 log10 (𝑁𝑐𝑜,𝐸𝑆 (𝑙𝑎𝑡)) + ∑ 𝐺𝑟𝑒𝑙,𝑟𝑥 − 𝐿𝑆
                                                                                      𝑖=1

This last term is only dependent upon the geometry (in particular the geocentric angle, ) and the gain
pattern in Recommendation ITU-R S.672 but not upon any of the non-GSO parameters and hence
can be pre-computed.
                                     𝐷   2
                                      𝐸𝑆                 𝑖=𝑁
                                   ( 100 ) 𝐹672 (𝑥) = ∑𝑖=1 𝐸𝑆 𝐺𝑟𝑒𝑙,𝑟𝑥 − 𝐿𝑆
A method to calculate based upon an assumed non-GSO ES separation distance ES_DISTANCE =
DES = 100 km is via the following Pade approximation:
                                                 𝑏𝑥+𝑐𝑥 2 +𝑑𝑥 3 +𝑒𝑥 4 +𝑓𝑥 5
                                         𝑦=𝑎+          1+𝑔𝑥+ℎ𝑥 2
```


---

## [Página 66]

```text
64                                      Rec. ITU-R S.1503-4

where:

                                              TABLE 10
                               F672 Pade approximation parameters
                                             Beamwidth =           Beamwidth =
      Parameter        Beamwidth = 4                                              Beamwidth = 1.5
                                             1.55 x < 35°         1.55 x > 35°
           a             −133.536851         −133.323814           −133.323814      −142.1952459
          b              0.001384021         0.017909858            0.02314611      −0.001235207
           c             0.000637798         −0.011981864          −0.001336397       0.00121213
          d              6.9531E-07          0.002350044           2.26511E-05      −4.77102E-05
           e            −1.94494E-07         −4.61428E-05          −6.95017E-08       6.5926E-07
           f             1.41944E-09          −3.30E-07            −7.75011E-10     −2.83069E-09
          g             −0.033027982         −0.408584467          −0.036720978     −0.033787173
          h              0.000434998         0.054553642           0.000370144       0.000306156


For the specific ES case the DES is not defined and so a value of 100 should be used while NUM_ES
set to 1 so this term does not contribute.
Given the F672 factor, the epfd(up) for a given geometry can be estimated using:
                                                                                    𝐷𝐸𝑆 2
    𝑒𝑝𝑑𝑓(𝑢𝑝) ≅ 𝑒. 𝑖. 𝑟. 𝑝. (𝑙𝑎𝑡) + 10 log10 (𝑁𝑈𝑀𝐸𝑆 ) + +10 log10 (𝑁𝑐𝑜,𝐸𝑆 (𝑙𝑎𝑡)) + (    ) 𝐹672 (χ)
                                                                                   100
D3.2.3.2 Worst pointing
The epfd calculation above relies on the ability to calculate the maximum e.i.r.p. towards the GSO.
As well as the e.i.r.p. mask, this will depend upon a number of additional factors including:
–       Latitude of the non-GSO ES
–        Minimum elevation angle(s) at that latitude 0[az, lat]
–        Exclusion zone size 0[lat]
–        Constellation (or sub-constellation) orbit parameters.
The influence of some of the factors can be seen in the examples below assuming the non-GSO ES
is in the northern hemisphere so that the centre of the Figure is pointing south.
```


---

## [Página 67]

```text
                                       Rec. ITU-R S.1503-4                                         65

Equatorial orbit, low latitude

                                               FIGURE 20
                                       Equatorial orbit low latitude




Here the non-GSO satellite never intersects the GSO orbit or the exclusion zone and so the min is the
angle at the non-GSO ES between the equatorial orbit and GSO arc in the azimuth of consideration.
Note that a purely equatorial orbit would be repeating so would be handled by a separate case within
the WCG. However it is included to show the edge case and gain an understanding of the geometry
involved.
Equatorial orbit, high latitude

                                               FIGURE 21
                                      Equatorial orbit high latitude




Here again the non-GSO satellite orbit never intersects the GSO orbit or exclusion zone but for the
azimuth of the GSO satellite the non-GSO satellite would not be active as it is below the minimum
elevation angle. Hence the min is between the nearest azimuth for which the non-GSO satellite would
be above the horizon. However this point would be considered as the applicable point for another
position on the GSO satellite where the minimum off-axis angle would be smaller and hence e.i.r.p.
```


---

## [Página 68]

```text
66                                      Rec. ITU-R S.1503-4

larger. It is therefore suggested that if the non-GSO satellite is below the minimum elevation angle
for that azimuth it need not be considered.
Polar orbit, low latitude
                                                FIGURE 22
                                          Polar orbit low latitude




In this case the polar orbiting satellite could be located at any pointing angle (az, el) as seen by the
non-GSO ES. The limiting case is then the edge of the exclusion zone, so that min = 0[lat].
Polar orbit, high latitude

                                                FIGURE 23
                                         Polar orbit high latitude




In this case the GSO arc is always below the minimum elevation angle and hence the minimum off-
axis angle min is the difference between the minimum elevation angle and elevation angle of the
GSO arc in the azimuth / latitude of interest.
```


---

## [Página 69]

```text
                                        Rec. ITU-R S.1503-4                                         67

Note this also the case for the previous scenario (polar orbit, low latitude) for the extreme edge case
where the GSO arc has a low elevation angle as seen by the non-GSO ES.
Low inclination, no in-line geometry

                                                FIGURE 24
                                    Low inclination, no in-line geometry




This example is similar to the equatorial case in that there is no in-line geometry, and hence the min
angle to use is the angular separation between the edge of the visible zone and GSO arc or the
exclusion zone angle 0[lat], whichever is larger.
Low inclination with in-line geometry

                                                FIGURE 25
                                    Low inclination with in-line geometry
```


---

## [Página 70]

```text
68                                      Rec. ITU-R S.1503-4

This is an extension of the previous case with the inclination increased until the visible zone spreads
either side of the GSO arc. The minimum off-axis angle is then the exclusion zone size so that
min = 0[lat].
General case
The geometry of the general case is shown in Fig. 26.

                                                FIGURE 26
                                        General case with orbit shell




The general method iterates over the field of view of the GSO satellite and determines the associated
ES location.
For non-GSO systems with non-repeating orbits, the result will be the same for all longitudes of the
GSO system, and hence a GSO longitude = 0° can be used.
For non-GSO systems with repeating orbit, an initial run should be undertaken and the average
longitude of each of the non-GSO satellites calculated over the repeat period together with the
likelihood of the non-GSO satellite being within 1 degree of that average longitude. The GSO satellite
should be located at the average longitude of the non-GSO satellite with the highest likelihood of
being within 1 degree of that satellite’s average longitude.
Then given:
–      GSO satellite longitude
–      Non-GSO ES φ (latitude, longitude)
–      Maximum or minimum latitude of the non-GSO satellite
–      Radius of the non-GSO satellite when at the maximum/minimum latitude.
It is then possible to iterate on the longitude of the non-GSO satellite which minimises the angle to
the GSO satellite as seen at the ES. This is the converse of the  angle and hence is called the  angle.
This can be derived using iteration or the analytic method as described in § D6.4.4.4. There will be
two positions, the + associated with the maximum latitude and the - associated with the minimum
latitude of the non-GSO system.
```


---

## [Página 71]

```text
                                           Rec. ITU-R S.1503-4                                        69

It is also possible to determine points that are in the direction of the + and - points with angle 0
from the GSO satellite as in Figs 27, 28 and 29. This results in points {a, b, c, d} that can then be
checked to see if they are valid, in particular that:
–       They are at least 0 away from the GSO satellite (arc)
–       They are within the [+, -] range of the non-GSO satellite as seen by the ES
–       They are above the minimum non-GSO elevation angle for the non-GSO ES latitude and
        azimuth of the test point.
If they are valid then they can be considered as possible options for the minimum off-axis angle
towards the GSO satellite at the non-GSO ES when pointing at a non-GSO satellite.

                                                   FIGURE 27
                             WCG(up) general case when [+, -] bracket the GSO arc




                                                   FIGURE 28
               WCG(up) general case when [+, -] do not bracket the GSO arc or intersect minimum 
```


---

## [Página 72]

```text
70                                          Rec. ITU-R S.1503-4

                                                    FIGURE 29
            WCG(up) general case when [+, -] do not bracket the GSO arc but does intersect minimum 




There is also a fifth test point {e} to handle the case when the GSO arc is below the minimum
elevation angle as shown in Fig. 30.

                                                    FIGURE 30
         WCG(up) general case when [+, -] bracket the GSO arc but it is below the minimum elevation angle




D3.2.3.3 WCG_Up_General
The general case iterates over the field of view of the victim GSO satellite as shown in Fig. 31.
```


---

## [Página 73]

```text
                                      Rec. ITU-R S.1503-4                                           71

                                               FIGURE 31
                                   Search of GSO satellite field of view




The search is undertaken in (, φ) as described below:

WCGA_UP_General:
        CheckCaseUpGeneral(0, 0)
        NumberOfPhiSteps = Integer(Degrees(φ0)/ 0.1)
        For PhiStep = 1 to NumberOfPhiSteps inclusive
            φ = φ0 * PhiStep / NumberOfPhiSteps
            ThetaStepSizeDegrees = 0.1 * φ0 / φ
            NumberOfThetaSteps = max(16, Integer(360 /                     ThetaStepSizeDegrees))
            For ThetaStep = 0 to NumberOfThetaSteps-1 inclusive
                 = radians(ThetaStepSizeDegrees * ThetaStep)
                CheckCaseUpGeneral(, φ)
            Next ThetaStep
        Next PhiStep


CheckCaseUpGeneral(, φ):
        Convert (, φ) to (az, el) at a GSO satellite set at longitude = 0
        Use (az, el) to create line from the GSO satellite
        Calculate the intersection point of that line and the spherical Earth
```


---

## [Página 74]

```text
72                                         Rec. ITU-R S.1503-4

        Calculate the (lat, long) of the non-GSO ES at the intersection point
        Check that the latitude is in the range of the non-GSO system i.e between
        ES_LAT_MIN and ES_LAT_MAX
        Check that the Nco(lat) > 0
        If the latitude is ok then
            If the non-GSO system uses a repeating orbit then
            {
                 Call WCGA_UP_SpecifcES_Repeating
                 Return
            }
            For this (lat, long) calculate the geocentric angle 
            For the GSO system’s beamwidth, calculate the F672()
            If the e.i.r.p. mask is defined by offaxis angle then
            {
                 Call CalcMinOffaxisAngle to calculate  for this location
                 If find a minimum offaxis angle then calculate the epfd(up) using:
                                                                     𝐷𝐸𝑆 2
                𝑒𝑝𝑓𝑑(𝑢𝑝) = 𝑒. 𝑖. 𝑟. 𝑝. (𝑙𝑎𝑡, ) + 10log10 (𝑁𝑈𝑀_𝐸𝑆) + 10log10 (𝑁𝑐𝑜 (𝑙𝑎𝑡)) + (
                                                                         ) 𝐹672 (χ)
                                                                     100
                 If this is the highest epfd(up) then store this value and (, φ)
            }
            Else the ES e.i.r.p. mask is defined by (az, el, DeltaLongES) then
            {


               Call   CalcES_e.i.r.p.(Non-GSO                    ES     lat,      long)        to   calculate
        e.i.r.p.(lat) for this location
                 Calculate the epfd(up) using:
                                                                                          𝐷𝐸𝑆 2
                𝑒𝑝𝑓𝑑(𝑢𝑝) = 𝑒. 𝑖. 𝑟. 𝑝. (𝑙𝑎𝑡) + 10𝑙𝑜𝑔10 (𝑁𝑈𝑀_𝐸𝑆) + 10𝑙𝑜𝑔10 (𝑁𝑐𝑜 (𝑙𝑎𝑡)) + (    ) 𝐹672 (χ)
                                                                                         100
                 If this is the highest epfd(up) then store this value and (, φ)


        Endif
        Endif


Note that the method to calculate the maximum latitude of the non-GSO system is given in § D3.2.3.6.
CalcES_e.i.r.p.(non-GSO ES lat, long)
        Calculate the DeltaLongES
        Create a vector rES of the non-GSO ES
        Set Maxe.i.r.p. = -9999
        Identify the ES_e.i.r.p. table for this non-GSO latitude
        Set StepSizeDeg = 0.5
        Set NumAz = 1 + integer(360 degrees / StepSizeDeg)
        Set NumEl = 1 + integer(90 degrees / StepSizeDeg)
        For all az_i = 0 to NumAz not inclusive
            Az = -180 + az_i * 360 / NumAz
```


---

## [Página 75]

```text
                                           Rec. ITU-R S.1503-4                                          73

              For all el_i = 0 to NumEl inclusive
                  El = 0 + el_i * 90 / NumEl
                  Create a vector razel in direction (az, el)
                  Select the appropriate es e.i.r.p. (az, el) array
             If the (az, el) direction is outside the exclusion zone and above
the minimum elevation for this azimuth and there could be a satellite in this
direction then
                  {
                       Calculate the ES_e.i.r.p.az,el towards the GSO for this (az, el,
DeltaLong)
                       If ES_e.i.r.p.az,el is greater than ES_e.i.r.p. then use this value
instead
                  }
              Next el_i
          Next az_i

To determine if there could be a non-GSO satellite in a given (az, el) direction from a non-GSO ES
at position rES, the following algorithm can be used, assuming that the vectors rES and razel are in Earth
centred coordinates (x, y, z):
Inputs:
          Non-GSO ES position vector = rES
          Vector in direction (az, el) = razel
          non-GSO satellite orbit inclination angle = i
          non-GSO satellite semi-major axis = a
          non-GSO satellite eccentricity = e
If the non-GSO satellite eccentricity = 0 (i.e. circular orbit):
         There could be a satellite in direction (az, el) if
              −i  Latitude(a)  +i
Otherwise, if the non-GSO satellite eccentricity > 0 (i.e. elliptical orbit):
       There could be a satellite in direction (az, el) if the following are both true:
              −i  Latitude(a(1+e))  +i
              −i  Latitude(a(1-e))  +i
Where the function CalcLatitude(d) is:
          Create a line from the non-GSO ES in direction (az, el) using parameter :
                                             𝒓 = 𝒓𝐸𝑆 + 𝜆𝒓𝑎𝑧𝑒𝑙
          The value of  for which the line is at distance d from the centre of the Earth can be calculated
          by solving the quadratic equation:
                                 2                             2
                                𝑟𝑎𝑧𝑒𝑙 λ2 + 2(𝒓𝐸𝑆 . 𝒓𝑎𝑧𝑒𝑙 )λ + 𝑟𝐸𝑆 − 𝑑2 = 0
          The root for which  > 0 should be used to calculate the position vector r.
          From this the latitude can be calculated using:
                                        Latitude(d) = asin(r.z / r)
```


---

## [Página 76]

```text
74                                       Rec. ITU-R S.1503-4

CalcMinOffaxisAngle(non-GSO ES lat, long)
        Calculate the (azGSO, GSO) of the GSO satellite as seen by the non-GSO ES
        Determine the radius Rn,+ of the non-GSO satellite when its lat = +i
        Determine the radius Rn,- of the non-GSO satellite when its lat = -i
        If Rn,+ or Rn,- are below the minimum operating height then determine the
        latitude of the non-GSO satellite when at this height and use this instead
        For each of {Rn,+, Rn,+} and {lat+, lat-}, calculated the {+, -} angles and
        associated non-GSO satellite positions identified as (a, b) in Figs. 27-29
        For each of the {+, -} positions, use spherical geometry to derive points
        (c, d) which have angle at the ES of 0 along the lines to point (a, b)
        respectively
        For each of (a, b, c, d) points, calculate the (azimuth, elevation) as seen
        by the ES
        Set the MinimumAngle to be +9999
        For each of points (a, b, c, d):
           If point is within {+, -} range and at least 0 away from GSO satellite
        and above the 0(lat, azimuth) then
             {
                 MinimumAngle = min(MinimumAngle, angle for this point)
             }
        Next point
        If {+, -} bracket the GSO arc and the elevation of the GSO satellite is
        less than the minimum elevation angle for the GSO satellite azimuth then
        {
             MinimumAngle = min(MinimumAngle, max(0, 0[Lat, AzGSO] - GSO))
        }
        Return MinimumAngle or if not found, an error code

D3.2.3.4 WCGA_UP_SpecifcES_Repeating
If there are specific ES locations and the non-GSO satellite network using a repeating track orbit then
there will be a highly limited number of geometries that are feasible. It is therefore possible to fly the
satellite for the repeat period and then, for each ES, for each non-GSO satellite, calculate the . If
 ≤ 0, or the elevation angle is below the minimum, then the ES would not transmit, otherwise the
epfd towards that location can be derived.
Not all geometries will be feasible. For example:
–        Non-GSO ES above 81.29° N or S will not see the GSO arc and so can be excluded.
–        There will be a maximum difference in longitude between that of the non-GSO ES
         determined by the edge of coverage (EOC) elevation angle of the GSO system.
–        The non-GSO ES will not transmit to the non-GSO satellite when it is within the exclusion
         zone defined by  < 0.
–        The non-GSO ES will not transmit to the non-GSO satellite when its elevation angle is below
         the minimum  < 0[lat, az].
–        The non-GSO satellite is below the minimum operating height hmin.
–        The number of non-GSO satellites that can ES at this latitude is zero.
–       These are therefore checked in the following algorithm:
WCGA_UP_SpecifcES_Repeating
```


---

## [Página 77]

```text
                                       Rec. ITU-R S.1503-4                                        75

        Calculate time step according to algorithm in § D4.3
        For t = 0 to repeat time of constellation
            Update positions of non-GSO satellites for this time step
            For each non-GSO ES
                If the non-GSO ES lat is <+81.29° && >-81.29° && Nco[Lat] > 0 then
                    For each non-GSO satellite
                         If satellite is visible and above minimum operating height
                              Calculate the elevation angle  and azimuth
                              Calculate the exclusion zone angle 
                              If (  0[lat, az] and   0[lat) then
                                      𝐸𝑃𝐹𝐷(𝑢𝑝) = 𝐸𝐼𝑅𝑃(, 𝑙𝑎𝑡) + 10log10 (𝑁𝑐𝑜 (𝑙𝑎𝑡))
                                      If this is the worst epfd so far then store this
        geometry
                              Endif
                          Endif
                      Next satellite
                Endif
            Next ES
        Next time step

D3.2.3.5 WCGA_UP_SpecifcES_NonRepeating
For the non-repeating case the non-GSO satellites will populate an orbit shell but only some of the
geometries considered in the general case will be feasible as the non-GSO ES will only be at specific
latitudes. Therefore this approach searches across the GSO arc as seen by each of the non-GSO ES
then takes a similar approach to the general case.

WCGA_UP_SpecifcES_NonRepeating:
     For each non-GSO ES
            If the non-GSO ES latitude is <+81.29° or >-81.29° then
                Calculate MaxDeltaLong = acos((Re/Rgeo)/cos(ES_lat))
                NumLongSteps = (integer)(degrees(MaxDeltaLong)/0.1°)
                For DeltaLongStep = -NumLongSteps to +NumLongSteps
                    DeltaLong = MaxDeltaLong * DeltaLongStep / NumLongSteps
                    GSO_Long = ES_Long + DeltaLong
                    Calculate (az, el) of ES as seen by GSO satellite
                    Convert (az, el) to (, φ)
                    Call CheckCaseUpGeneral(, φ)
                Next DeltaLongStep
       Endif
     Next ES
```


---

## [Página 78]

```text
76                                      Rec. ITU-R S.1503-4

D3.2.3.6 Range in latitude
When calculating the WCG(up) and also the epfd(up), it is necessary to identify where the ES could
be located. While most systems in types A and B have global coverage, non-GSO networks of type C
will be limited in the latitude range. For systems with multiple sub-constellations, this range could
vary between sub-constellations.
The latitude range can be derived from the satellite’s height, its inclination angle and the minimum
operating elevation angle for the ES, as in Fig. 32 below.

                                                FIGURE 32
                                  Calculation of maximum latitude for ESs




For elliptical systems, there will be two values, one for the apogee and another for the perigee, and
so the inputs would be:
         Semi-major axis of orbit (km):                   a
         Eccentricity of orbit:                           e
        Minimum elevation angle (radians):                     
        Inclination angle (radians):                           i
From these parameters, the following calculations can be undertaken:
                                            𝑟𝑎 = 𝑎(1 + 𝑒)
                                                     π
                                              ψ= 2+ ε
                                                         𝑅𝑒
                                        φ𝑎 = sin–1 (          sin ψ)
                                                         𝑟𝑎

                                         θ𝑎 = π − (ψ + φ𝑎 )
Then:
                                           𝐿𝑎𝑡max = 𝑖 + θ𝑎
Similarly, using:
                                            𝑟𝑝 = 𝑎(1 − 𝑒)
```


---

## [Página 79]

```text
                                         Rec. ITU-R S.1503-4                                             77

Using the same equations but replacing suffix (a) with (p), the following can be derived:
                                           𝐿𝑎𝑡min = −𝑖 − θ𝑝
This assumes that for elliptical systems, the apogee is in the northern hemisphere, i.e. that one of the
following is true:
        e=0
          = 270°
where:
         : argument of perigee.
In the case that:
         e>0
          = 90°
Then the following adjustments should be made:
         Latmax’ = –Latmin
         Latmin’ = –Latmax
In the case that the orbit inclination is zero and eccentricity zero (i.e. for an equatorial circular orbit)
then these equations reduce to:
                                               𝐿𝑎𝑡max = θ
                                              𝐿𝑎𝑡min = −θ

D3.3     WCG epfdIS
D3.3.1 Inputs
The inputs to the algorithm are as follows:
      SS_e.i.r.p.: the satellite e.i.r.p. mask to check
             θadB : the GSO satellite’s beam half power beamwidth
                :    the minimum elevation angle of the GSO system
     a,i,e, ,, :   the orbit parameters of the non-GSO system.
The GSO satellite’s beam half power beamwidth and minimum elevation angle can be selected using
the same method as in § D3.2.2 for the WCG epfd(up).
If there are multiple sub-constellations with alternative orbital elements then the process should be
repeated for each unique set of {a, e, i}.
D3.3.2 Algorithm

WCGA_IS:
     From the epfd limits get the gain pattern to use
       From the epfd limits get the GSO beamwidth θadB
       From θadB calculate φ1, φ2
       Using the gain pattern calculate Grel(φi) for i = 1,2
       From φ1 calculate LatBS
       If for all satellites i = 0 then
       {
         Worst Case Geometry:
```


---

## [Página 80]

```text
78                               Rec. ITU-R S.1503-4

          BS.Latitude = 0
          BS.Longitude = LatBS
          GSO.Longitude = 0
     }
     Else
     {
       Set WorstEPFDBin = -9999
       Set WorstAngularVelocity = +9999
       For all satellites in the order listed in ITU DB
       {
          Determine e.i.r.p. mask to use for this satellite
          If this e.i.r.p. mask has not been checked so far then
          Call GetWCGA_IS(e.i.r.p._mask, i)
       End if
     Next satellite
     }
     If no solution has been found then
     {
      Convert (=i, φ=φ1) to (az, el)
      Using (az, el), create line from the GSO satellite
      Put the ES at the first intersection point with the Earth
     }
     Rotate GSO, BS in longitude to ensure inline event

GetWCGA_IS(e.i.r.p._Mask, i):
     LatStep = i / RoundUp(i) in degrees
     For lat=−i to +i in LatStep steps
     {
       Set satellite at latitude to calculate r, v
       If satellite is above minimum operating height
       {
          From r, φi calculate ψi
          From φi, ψi calculate Di, θi
          Try to calculate ∆longi
          In the cases that the geometries are feasible
          {
              From the GSO gain pattern calculate Grel(φi)
              From the e.i.r.p. mask calculate e.i.r.p.(ψi)
              Calculate epfdi
             Calculate rgso, gso
             Calculate θ of non-GSO satellite as seen by GSO
             If epfdi is higher than Worstepfd
             {
              Store this geometry
              WorstAngularVelocity = θ
              Worstepfd = epfdi
             }
             Else if epfdi is the same bin as Worstepfd
             {
              If θ is lower than WorstAngularVelocity
              {
                Store this geometry
                WorstAngularVelocity = θ
```


---

## [Página 81]

```text
                                         Rec. ITU-R S.1503-4                                            79

                   }
                  }
              }
          }
      }


D3.3.3 Geometry
There are two potentially significant geometries, which is when the non-GSO satellite just becomes
visible as seen by the GSO satellite and when it traverses the GSO satellite beam, as in Fig. 33.

                                                 FIGURE 33
                                   Two WCG(IS) non-GSO satellite locations




In some cases, the same WCG location will handle both geometries – e.g. for an equatorial satellite
system a beam at the extreme in azimuth will be aligned for both geometries.
From the radius of the non-GSO satellite at each of the positions, it is possible to calculate the off-axis
angle at the satellite and hence e.i.r.p.() together with the distance as in Fig. 34.
```


---

## [Página 82]

```text
80                                         Rec. ITU-R S.1503-4

                                                   FIGURE 34
                                Two WCG(IS) calculation of satellite off-axis angle




where:
                                           φ1 = φ𝐵𝑆 (from above)
                                                                𝑅
                                                sin φ2 = 𝑅 𝑒
                                                                 𝑔𝑠𝑜

Hence:
                                                         𝑅𝑔𝑒𝑜
                                           sin ψ𝑖 = 𝑅             sin φ𝑖
                                                         𝑛𝑔𝑠𝑜,𝑖
                                       π                     π
where i = {1, 2} noting that ψ1 > 2 and that ψ2 < 2 so that:
                                                             𝑅𝑔𝑒𝑜
                                     ψ1 = π − sin−1 [ 𝑅                  sin φ1 ]
                                                             𝑛𝑔𝑠𝑜,1

                                                          𝑅𝑔𝑒𝑜
                                        ψ2 = sin−1 [ 𝑅              sin φ2 ]
                                                          𝑛𝑔𝑠𝑜,2

Then:
                                             θ𝑖 = π − φ𝑖 − ψ𝑖
                                                                 sin θ
                                             𝐷𝑖 = 𝑅𝑛𝑔𝑠𝑜,𝑖 sin φ𝑖
                                                                         𝑖

Hence given a non-GSO satellite with radius distance Rngso,i at the two specified geometries, the two
single entry epfd levels can be calculated as:
                           𝑒𝑝𝑓𝑑𝑖 = 𝑒. 𝑖. 𝑟. 𝑝. (ψ𝑖 ) + 𝐺𝑟𝑒𝑙,𝑖 – 10 log10 (4π𝐷𝑖2 )
Note that 𝐺𝑟𝑒𝑙,1 = 0 and 𝐺𝑟𝑒𝑙,2 = 𝐺𝑟𝑒𝑙 (φ2 − φ1 ) and D is in metres.
By setting the non-GSO satellite at a specified latitude, lat, (where it has longitude long), it is therefore
possible to derive the single entry epfd from the radius vector and the two geometries identified above.
```


---

## [Página 83]

```text
                                        Rec. ITU-R S.1503-4                                          81

In some cases, there will be no in-line geometry – for example for elliptical systems at apogee the
line from the non-GSO satellite to the GSO arc will at no point intersect the Earth. This can be checked
by calculating the difference in longitude between the non-GSO satellite and the point on the GSO
arc from the geometry above and the non-GSO satellite’s latitude using:
                                                         cos θ
                                        cos ∆𝑙𝑜𝑛𝑔𝑖 = cos 𝑙𝑎𝑡𝑖
                                                                 𝑖

If there is no solution to this equation, then there is no position that meets the required geometry.
Other positions could be excluded if the non-GSO satellite was below the minimum operating height.
If there is a solution, then the position of the non-GSO and GSO satellite can be calculated from:
                                               cos(𝑙𝑎𝑡)cos(𝑙𝑜𝑛𝑔)
                                𝑟𝑛𝑔𝑠𝑜 = 𝑅𝑛𝑔𝑠𝑜 ( cos(𝑙𝑎𝑡)sin(𝑙𝑜𝑛𝑔) )
                                                     sin(𝑙𝑎𝑡)
                                                cos(𝑙𝑜𝑛𝑔 − 𝑙𝑜𝑛𝑔)
                                 𝑟𝑔𝑠𝑜 = 𝑅𝑔𝑠𝑜 ( sin(𝑙𝑜𝑛𝑔 − 𝑙𝑜𝑛𝑔) )
                                                         0
For the i = 1 case the boresight is where the line L from the GSO to non-GSO satellite intersects the
Earth’s surface.
                                   𝐿1 (λ) = 𝑟𝑔𝑠𝑜 + λ(𝑟𝑛𝑔𝑠𝑜 − 𝑟𝑔𝑠𝑜 )
For the i=2 case the boresight is on a line created using an adjusted non-GSO satellite position,
calculated using:
                                                          sin φ
                                       𝑅′𝑛𝑔𝑠𝑜,2 = 𝑅𝑔𝑒𝑜
                                                         sin ψ′2
where:
                                         ψ′2 = π − φ1 − θ2
Given multiple locations with the same single entry epfd, then the one to use would be the one with
the lowest angular velocity, using the same method as for WCGA(down), noting that the velocity
vector of the GSO satellite can be derived in the same way as for earth stations, i.e.:
                                                       –𝑦
                                          𝑔𝑠𝑜 = 𝑤𝑒 ( 𝑥 )
                                                       𝟎
The position of the GSO satellite would be chosen such that one of the non-GSO satellites with the
e.i.r.p. mask identified traverses the critical geometry during its first orbit, using the same
methodology as for the WCGA(up).
Additional geometry for the WCG for epfd(IS) is described in §§ D3.1.3 and D3.2.3 above.


D4       Calculation of time step size and number of time steps

D4.1     Simulation time increment and accuracy
The simulation time increment is one of the most important parameters for the determination of
a distribution function of interference from non-GSO networks on the basis of the simulation model.
Its specified value should guarantee absence of cases when high-level short-term interference
exceeding an acceptable level is missed and is not considered. Otherwise results of simulation
analysis will be inaccurate and sometimes erroneous. The decrease of a simulation time increment
```


---

## [Página 84]

```text
82                                            Rec. ITU-R S.1503-4

allows to increase accuracy of obtained results but at the same time results in increase of total number
of simulation time increments and amount of required calculations.
The description of calculation algorithms for simulation time increment in uplink, downlink and
intersatellite cases are shown below.
The algorithms are based upon a set of orbital characteristics, such as the inclination angle. If there
are multiple sets, e.g. for multiple sub-constellations, then the longest run time and smallest time step
over all sub-constellations should be used.
When an artificial precession is used, the same value should be used for all satellites of the non-GSO
system. The value used should be the one calculated for the satellite used to derive the time step size
and run duration.
In order to reduce run times, the following procedure should be used to adjust the time step size for
non-repeating orbits cases where the number of time steps exceeds 1e8:
         Calculate time step and run time using Nhit = 16
         If orbit is non-repeating
            If number of time steps exceeds 1e8 then:
                  ′                  𝑁ℎ𝑖𝑡
                 𝑁ℎ𝑖𝑡 =
                          𝑚𝑖𝑛(𝑁𝑐𝑜𝑢𝑟𝑠𝑒 ,√𝑁𝑠𝑎𝑡𝑒𝑙𝑙𝑖𝑡𝑒𝑠 )

                 Re-calculate time step and run time
                  ′                    𝑁′
                 𝑁𝑐𝑜𝑢𝑟𝑠𝑒 = 𝑓𝑙𝑜𝑜𝑟 ( ℎ𝑖𝑡 𝑁𝑐𝑜𝑢𝑟𝑠𝑒 )
                                       𝑁ℎ𝑖𝑡
                   ′
                 𝑇𝑆𝑐𝑜𝑢𝑟𝑠𝑒 = 𝑇𝑆 ′ . 𝑁𝑐𝑜𝑢𝑟𝑠𝑒
                                    ′

            Endif
         Endif

D4.2     Description of the procedure for the determination of minimum downlink simulation
         time increment
The value of simulation time increment should guarantee acquisition and description of the most
short-term interference scenarios with the required accuracy. The high-level short-term interference
is caused by emission of a non-GSO space station which is in-line situation (a non-GSO satellite
passes through the main beam of a GSO earth station antenna). Therefore, the method for determining
a simulation time increment tref is based on ensuring the required number Nhit of epfd↓ estimations
during the time interval t when a non-GSO satellite passes through the main beam of a GSO earth
station antenna:
                                                                Δ𝑡
                                                        Δ𝑡𝑟𝑒𝑓 = 𝑁                                    (1)
                                                                    ℎ𝑖𝑡

tref should be rounded to the nearest non-zero millisecond.
The time required for a non-GSO satellite passing through the main beam of a GSO earth station
antenna depends on mutual location of the earth and space stations of the GSO network as well as on
orbital parameters of the non-GSO network. The value of t should be computed at the location where
the time for a non-GSO satellite to pass through the GSO main beam is smallest. Since this occurs
when a GSO earth station is located directly under a GSO space station, pass time t is determined
by equations (2) and (3) (see Fig. 35).
                                                               2φ
                                                         ∆𝑡 = ω                                      (2)
where:
```


---

## [Página 85]

```text
                                           Rec. ITU-R S.1503-4                                               83

                                       1                𝑒    𝑅           1
                                 φ = 2 θ3dB – arcsin [𝑅 +ℎ sin (2 θ3dB )]
                                                             𝑒


                                 =      ( s cos(i) – e )2 + (s sin (i))2                                (3)
                                                          0.071
                                             s =
                                                    ( Re + h) Re 1.5
                s:    non-GSO satellite angular velocity of rotation around the Earth at the minimum
                       operational altitude (degrees/s); for multiple orbits, the greatest such s should
                       be selected
                 e:   Earth rotation angular velocity at the equator (degrees/s)
                  i:   orbit inclination (degrees)
              3dB:    GSO earth station antenna 3 dB beamwidth (degrees)
                Re:    Earth radius (km)
                 h:    orbit altitude (km) (see Note 1).

NOTE 1 – In the case that the constellation has multiple values of h for different sub-constellations or planes,
the lowest value should be used. In the case of elliptical orbits the minimum operating height should be used.


                                                    FIGURE 35
                                      Calculation of epfd(down) time step size
```


---

## [Página 86]

```text
84                                         Rec. ITU-R S.1503-4

The Nhit value defines simulation accuracy. The higher the Nhit value the higher the accuracy of the
final results.
Nhit should be set to 16 as derived in § D4.5. In a case when a non-GSO network satellite constellation
consists of satellites with different orbital parameters it is necessary to determine a simulation time
increment for each type of the orbits concerned and to define a minimum one.

                                                 TABLE 11
                                                 Input data
                              Parameter                                 Designation         Units
 Orbit inclination                                                           i             degree
 Orbit altitude or for elliptical orbits the minimum operating height        h               km
 GSO earth station antenna 3 dB beamwidth                                  3dB            degree
 Number of epfd↓ calculations required during the time a non-GSO            Nhit              –
 satellite is passing through the main beam of a GSO earth station
 antenna

D4.3    Description of the procedure for the determination of minimum uplink simulation time
        increment
High-level short-term uplink interference would be caused by emissions from a non-GSO earth
station during an in-line event (when a GSO SS is in the main beam of a non-GSO earth station
antenna). The required number Nhit of epfd↑ measurements should be effected within the period of
the GSO satellite staying in the main beam of the non-GSO earth station antenna to ensure acquisition
and definition of the in-line event. If the non-GSO earth station were directly below the GSO satellite
(see Fig. 36) then the value of the minimum simulation time increment could be calculated using
equations (1) and (2). In that case a width of the non-GSO earth station antenna main beam should be
taken instead of that of the GSO earth station antenna main beam.
```


---

## [Página 87]

```text
                                        Rec. ITU-R S.1503-4                                         85

                                                 FIGURE 36
                                    Calculation of epfd(up) time step size




                                               TABLE 12
                                               Input data
                          Parameter                                          Designation   Units
 Orbit inclination                                                                i        degree
 Orbit altitude                                                                   h         km
 Non-GSO earth station antenna 3 dB beamwidth                                   3dB       degree
 Number of epfd calculations required during the time a GSO                      Nhit        –
 satellite is passing through the main beam of a non-GSO earth
 station antenna


D4.4     Description of the procedure for the determination of minimum inter-satellite
         simulation time increment
The time step size for epfdis calculations is derived by considering that there should be at least Nhit
time steps during which the non-GSO satellite is within the main beam of the GSO satellite. Given
```


---

## [Página 88]

```text
86                                      Rec. ITU-R S.1503-4

that the shortest time step is when the beam at the GSO is pointed as far as possible away from
the sub-satellite point, and given the following:
                Re: radius of the Earth
                 h: height of non-GSO orbit
              Rgeo: radius of geostationary orbit
             3dB:   half power beamwidth of GSO beam.
For elliptical orbit systems, calculate the height when the non-GSO satellite crosses the equator,
i.e. when  = − or + such that (+) = 0 or integer multiples of . In addition, it is necessary to
consider the minimum operating height, so that the height to use is the maximum of {minimum
operating height, height when crossing the equator}.
Then the time step can be calculated using the following algorithm (see Fig. 37).

                                                FIGURE 37
                               Geometric parameters involved in the equations




Calculate:
                                                      R 
                                         1 = arcsin  e 
                                                     R 
                                                      geo 
                                                                  Rgeo 
                                  2 = 180 – arcsin  sin ( 1 )        
                                                                 Re + h 

                                        3 = 180 – ( 1 + 2 )
```


---

## [Página 89]

```text
                                        Rec. ITU-R S.1503-4                                          87


                                                            sin 3
                                          D1 = (Re + h)
                                                            sin 1
                                                             θ
                                         𝐷2 = 2𝐷1 sin ( 3dB )
                                                         2

                                       𝐷3 = 𝐷2 cos(180 − θ2 )
Then calculate the value:
                                                        3        𝐷 /2
                              φ = 2 arctan [(𝑅 +ℎ) –(𝐷 /2)               ]                          (4)
                                                  𝑒        sin(180 – θ )
                                                             2          2


which can be used in equation (2) to calculate the step size to use.

D4.5    Derivation of Nhit
The time step size is selected to ensure there is sufficient resolution of the epfd within the victim’s
main beam. The necessary resolution is driven by the bin size of 0.1 dB and from this the number of
steps within the main beam can be derived.
Nhit should be selected so that the highest epfd will be detected in the simulation and identified to be
within the right bin. This implies a resolution in the calculations of (0.1 dB)/2 = 0.05 dB.
The greatest error would occur when two-time steps are located equally spaced on either side of the
main beam, as in Fig. 38.

                                               FIGURE 38
                                            Calculation of Nhits




The spacing between samples will be:
                                                       θ
                                              ∆θ = 𝑁3dB
                                                           ℎ𝑖𝑡

The gain pattern within the main beam can be assumed to be parabolic and hence following:
```


---

## [Página 90]

```text
88                                       Rec. ITU-R S.1503-4

                                                                  θ        2
                                           𝐺𝑟𝑒𝑙 = 12 (θ                 )
                                                                  3dB

The slope on this curve is:
                                             Δ𝐺𝑟𝑒𝑙         24
                                                     =                θ
                                              Δθ         θ3dB 2

Hence, Nhit required for a difference in gain of 0.05 dB can be derived as follows:
                                                     θ            ∆θ                  θ       1
                          ∆𝐺𝑟𝑒𝑙 = 0.05 = 24 ∙ θ            ∙ θ             = 24 ∙ θ         ∙ 𝑁
                                                     3dB          3dB                 3dB         ℎ𝑖𝑡

So:
                                                                       θ
                                           𝑁ℎ𝑖𝑡 = 480 ∙ θ
                                                                      3dB

Therefore for the time step nearest the main beam when:
                                                         1θ
                                               θ = 2 𝑁3dB
                                                              ℎ𝑖𝑡

Then:
                                    𝑁ℎ𝑖𝑡 = 𝑅𝑜𝑢𝑛𝑑𝑈𝑝[√240 ] = 16
This approach can also be used for cross-track sampling, and hence Ntrack = Nhit = 16.

D4.6    Total simulation run time
This section describes the calculation of number of time steps for the epfd↓ and epfd↑ algorithms
specified in § D5. The basic approach first considers constellations with repeating and non-repeating
ground track separately, where systems that use repeating ground tracks use station keeping to ensure
that the satellites follow a single Earth trace. For example there will be minor launch errors and
perturbations that would make the orbit drift unless station keeping was used operationally to ensure
the track repeats. Administrations therefore must indicate to the BR whether station keeping is used
to maintain a single track.
Some constellations have different values for inclination, height, or eccentricity between the planes.
In this case, it is assumed that, to maintain coverage, the constellation would be designed so that the
separation between planes does not change significantly. For the case of repeating ground tracks, this
means that there will be a single repeat period for the constellation. This is the time for all satellites
within the constellation to return to the same position relative to the Earth and each, within the
constraints of station keeping. For the case of non-repeating ground tracks, there will be a single
period for all the orbit planes to process around the equator.
This approach is to be used for constellations with both circular and elliptical orbits with orbit
inclination non-zero. For equatorial orbit constellations in which all satellites have the same altitude,
then it is sufficient to run for a single repeat period. This can be calculated as follows:
                                                              2π
                                             𝑇𝑟𝑢𝑛 = 𝑤 – 𝑤
                                                              𝑠        𝑒

                                                                           Trun
                                        N steps = RoundDown
                                                                           Tstep
where ws and we are the rotation angular velocities of the satellite and Earth as given in § D2.
Table 13 shows the input parameters used in all other constellation types.
```


---

## [Página 91]

```text
                                         Rec. ITU-R S.1503-4                                         89

                                                TABLE 13
                                                Input data
                            Parameter                                     Designation     Units
  Constellation repeats yes/no                                                Type          –
  Minimum samples taken to get statistical significance                     NS = 10         –


In both cases the time step can be calculated using the method described above. The number of time
steps should be at least:
        Nmin =    NS  /(100 – (maximum % in the Tables of Article 22 of the RR less than
                  100%))
So, for example, for the 99.999% case the number of steps would be:
                                           𝑁𝑚𝑖𝑛 = 1 000 000
D4.6.1 Repeating orbits
For those orbits specified as being repeating, the orbit predictor has to be accurate to ensure
repeatability. Thus, there is an option for administrations to specify the exact longitude precession
rate with respect to a point mass orbit predictor that ensures the orbit would repeat. The definition
and use of this parameter is described in § D6.3.
With this parameter, a simulated orbit would be repeated but in reality there would be a slight drift
due to longitudinal station keeping errors. It is expected that station keeping changes within the orbit
plane would make no difference and so are not included.
The result should be as shown in Fig. 39.

                                                 FIGURE 39
                        Track of repeating orbit non-GSO satellite through GSO ES beam




In Fig. 39, it can be seen that the result would be a series of samples within the main beam of the
GSO earth station that is sufficiently fine to resolve the main beam, includes station keeping drift and
produces sufficient samples to generate the required statistics.
```


---

## [Página 92]

```text
90                                         Rec. ITU-R S.1503-4

                                                 TABLE 14
                                                 Input data
                              Parameter                        Designation           Units
            Constellation repeat period                              Prepeat           s


Given the following parameters:
             Nmin: minimum number of time steps required for statistical significance
            Prepeat: time period that the constellation repeats (s)
              Tstep: time step (s)
            Ntracks: number of tracks through the main beam, = 16, as specified in § D4.5.
For this case the time step should not exactly divide the constellation repeat period. If:
                                          𝑁𝑟𝑒𝑝𝑠𝑡𝑒𝑝𝑠 = 𝑃𝑟𝑒𝑝𝑒𝑎𝑡 ⁄𝑇𝑠𝑡𝑒𝑝
is an integer, then calculate a revised time step (used in the following equations in place of Tstep) equal
to:
                                ′
                               𝑇𝑠𝑡𝑒𝑝 = 𝑇𝑠𝑡𝑒𝑝 (1 + 𝑁𝑟𝑒𝑝𝑠𝑡𝑒𝑝𝑠 )/ 𝑁𝑟𝑒𝑝𝑠𝑡𝑒𝑝𝑠
Calculate the time period required to get the minimum number of time steps for statistical
significance:
                                             𝑇𝑠𝑖𝑔 = 𝑁𝑚𝑖𝑛 ∙ 𝑇𝑠𝑡𝑒𝑝
This corresponds to the following number of constellation repeats:
                           Nrep = round (Tsig/Prepeat) to nearest integer above.
The number of repeats of the constellation is the largest of Nrep or Ntracks i.e.:
                                          Nrun = max (Nrep, Ntracks)
Then the total run time is:
                                             Trun = Nrun · Prepeat
So the number of time steps is then:
                           Nsteps = round (Trun/Tstep) to nearest integer below
                                             Trun = Nsteps * Tstep
D4.6.2 Non-repeating orbits
In this case the longitudinal spacing between successive ascending node passes must be examined so
that there are sufficient tracks within the main beam. The time step size and number of time steps can
be used to determine how far a particular orbit will have processed within the run. The same numbers
can be used to determine how many time steps are required for the orbit to drift around the equator.
The orbit period can then be used to work out the difference between tracks.
The constant that specifies the required number of points within the main beam can be used to specify
the number of tracks though the main beam required (i.e. Ntrack = Nhits). If the gap between tracks is
too large or too fine (resulting in either insufficient samples or excessive run times respectively) then
artificial precession can be used.
It is expected that station keeping drift should cancel out in the long term and so would not be required
for these calculations.
```


---

## [Página 93]

```text
                                             Rec. ITU-R S.1503-4                                                  91

The result should be as shown in Fig. 40.

                                                     FIGURE 40
                          Track of non-repeating orbit non-GSO satellite through GSO ES beam




In Fig. 40, it can be seen that the result would be a series of tracks within the main beam of the GSO
earth station that is sufficiently fine to resolve the main beam and produces sufficient samples to
generate the required statistics.

                                                    TABLE 15
                                                    Input data
                                Parameter                                        Designation             Units
 Orbit inclination                                                                      i                degree
 Orbit semi-major axis                                                                 a                  km
 GSO earth station antenna 3 dB beamwidth(1)                                          3dB               degree
 Required number of tracks of a non-GSO satellite passing through the                Ntracks               –
 main beam of a GSO earth station antenna
(1)
      In the case of calculating run length for epfd↓. In the case of epfdis and epfd↑:
      epfd↑:calculate  using the beamwidth of the non-GSO earth station as specified in its e.i.r.p. mask using
      the calculation in equation (3)
      epfdis: calculate  using the beamwidth of the GSO satellite in the calculation in equation (4).

Two parameters are required:
               Spass   longitudinal spacing between successive ascending passes through the equatorial
                        plane
                Sreq   required resolution of passes through the equatorial plane based upon GSO earth
                        station beam size.
These are calculated using the following steps:
Step 1: Using the equations in § D6.3.2, calculate the 𝑛, Ω𝑟 , ω𝑟 in radians / second
Step 2: Convert the 𝑛, Ω𝑟 , ω𝑟 into degrees per minute
```


---

## [Página 94]

```text
92                                      Rec. ITU-R S.1503-4

Step 3: Calculate the nodal period of the orbit in minutes using:
                                                         360
                                               𝑃𝑛 = 𝑤 + 𝑛
                                                            𝑟

Step 4: Calculate the longitudinal spacing between successive ascending passes through the
        equatorial plane, S, given the Earth’s rotation rate (e = 0.250684 degrees/min):
                                𝑆𝑝𝑎𝑠𝑠 = (Ω𝑒 − Ω𝑟 ) ∙ 𝑃𝑛                   degrees
        The equations above apply to circular orbits. For elliptical orbit systems where the
        calculations above would be significantly different, the value of Spass should be supplied by
        the administration.
Step 5: From the GSO earth station beamwidth and height, Sreq can be calculated using equation (3):
                                                                2φ
                                              𝑆𝑟𝑒𝑞 = 𝑁
                                                            𝑡𝑟𝑎𝑐𝑘𝑠

Step 6: Calculate the number of orbits to fully populate around the equator, taking into account that
        each plane has ascending and descending nodes:
                                                                180
                                              𝑁𝑜𝑟𝑏𝑖𝑡𝑠 = 𝑆
                                                                    𝑟𝑒𝑞

Step 7: Round Norbits to next highest integer.
Step 8: Calculate total angle orbit has rotated round during this time:
                                        𝑆𝑡𝑜𝑡𝑎𝑙 = 𝑁𝑜𝑟𝑏𝑖𝑡𝑠 ∙ 𝑆𝑝𝑎𝑠𝑠
Step 9: Calculate the number of multiples of 360 that this corresponds to, rounding to nearest integer
        below:
                                                       𝑡𝑜𝑡𝑎𝑙    𝑆
                                          𝑁360 = int ( 360   )
Step 10: Calculate the separation between planes that this corresponds to:
                                                            360𝑁360
                                          𝑆𝑎𝑐𝑡𝑢𝑎𝑙 = 𝑁
                                                                𝑜𝑟𝑏𝑖𝑡𝑠

Step 11:    To ensure that the orbit drifts with the required precession rate, the following additional
            artificial precession should be included:
                          𝑆𝑎𝑟𝑡𝑖𝑓𝑖𝑐𝑖𝑎𝑙 = 𝑆𝑎𝑐𝑡𝑢𝑎𝑙 − 𝑆𝑝𝑎𝑠𝑠                   degrees/orbit
            or:
                                              𝑆𝑎𝑟𝑡𝑖𝑓𝑖𝑐𝑖𝑎𝑙
                              𝐷𝑎𝑟𝑡𝑖𝑓𝑖𝑐𝑖𝑎𝑙 =                               degrees/s
                                               𝑇𝑝𝑒𝑟𝑖𝑜𝑑

Step 12:    Part D gives more information on how this parameter is used. The total run time is then
            the time to process around the equator using the orbit period from either §§ D6.3.1 or
            D6.3.2 depending upon or bit model, namely:
                                       T’run = Tperiod · Norbits
Step 13:    The total number of time steps is then:
                        Nsteps = Round (T’run / Tstep) to nearest integer below
                                          Trun = Nsteps * Tstep.
```


---

## [Página 95]

```text
                                          Rec. ITU-R S.1503-4                                          93

D4.7    Dual time step option
D4.7.1 Dual time step option epfd(down)
In order to improve simulation performance an option to the algorithm is to implement two time steps.
A coarse time step would be used except when any non-GSO satellite is near the main beam of the
GSO ES or edge of exclusion zone, defined as:
                           GRX() > min[Gmax − 30 dB, GRX(0[Latitude]])
Figure 41 shows where to use the finer time step:

                                                  FIGURE 41
                              Use of fine time step when at with GSO ES main beam




A coarse step size is used for non-critical regions far from the GSO earth station main beam. This
step size is defined as a topocentric angle:
                                              φ𝑐𝑜𝑎𝑟𝑠𝑒 = 1.5°
This coarse step size is used for all antenna beamwidths and all non-GSO systems.
The size of the coarse step needs to be an integer multiple of fine steps for statistical purposes. Since
the coarse step size is constant, the ratio of coarse steps to fine steps is dependent only upon the
beamwidth of the GSO earth station (3dB). This ratio is defined as:
                              𝑁𝑐𝑜𝑎𝑟𝑠𝑒 = 𝐹𝑙𝑜𝑜𝑟((𝑁ℎ𝑖𝑡𝑠 ∗ φ𝑐𝑜𝑎𝑟𝑠𝑒 )⁄φ3dB )
where floor is a function that truncates the decimal part of the ratio and outputs the integer part of the
ratio. This produces a conservative ratio of fine steps to coarse steps to ensure that a coarse step is
never larger than the target topocentric size of 1.5°.
D4.7.2 Dual time step option epfd(up)
In order to improve simulation performance an option to the algorithm is to implement two-time
steps. A coarse time step would be used except when the gain from any non-GSO ES towards the
GSO satellite is greater than −30 dB.
A coarse step size is used for non-critical regions when the non-GSO earth station main beam is
pointing away from the GSO satellite. This step size is defined as a topocentric angle:
                                              φ𝑐𝑜𝑎𝑟𝑠𝑒 = 1.5°
This coarse step size is used for all antenna beamwidths and non-GSO systems.
```


---

## [Página 96]

```text
94                                       Rec. ITU-R S.1503-4

The size of the coarse step needs to be an integer multiple of fine steps for statistical purposes. Since
the coarse step size is constant, the ratio of coarse steps to fine steps is dependent only upon the
beamwidth of the non-GSO earth station (3dB). This ratio is defined as:
                              𝑁𝑐𝑜𝑎𝑟𝑠𝑒 = 𝐹𝑙𝑜𝑜𝑟((𝑁ℎ𝑖𝑡𝑠 ∗ φ𝑐𝑜𝑎𝑟𝑠𝑒 )⁄φ3dB )
where floor is a function that truncates the decimal part of the ratio and outputs the integer part of the
ratio. This produces a conservative ratio of fine steps to coarse steps to ensure that a coarse step is
never larger than the target topocentric size of 1.5 degrees.


D5      epfd calculation description

D5.1    epfd↓ software description
This section describes the algorithm to calculate the epfd↓ from a non-GSO constellation into a GSO
downlink. It is assumed that each non-GSO satellite has a pfd mask. From the pfd for each satellite
the aggregate epfd↓ at an earth station of a GSO system is calculated. This is repeated for a series of
time steps until a distribution of epfd↓ is produced. This distribution can then be compared with the
limits to give a go/no go decision.
Figure 42 shows the geometry with constellation of non-GSO satellites and test GSO satellite
transmitting to a GSO earth station.

                                                FIGURE 42
                                       Example of epfd(down) scenario
```


---

## [Página 97]

```text
                                           Rec. ITU-R S.1503-4                                                 95

D5.1.1 Configuration parameters
These run definition parameters are:

               Parameter name                        Parameter value           Parameter units and ranges
 Frequency                                               F_DOWN                              MHz
 GSO satellite longitude                                GSO_LONG                            degree
 GSO earth station latitude                            GSO_ES_LAT                           degree
 GSO earth station longitude                          GSO_ES_LONG                           degree
 GSO earth station dish size                         GSO_ES_D_ANT                              m
 GSO earth station gain pattern                     GSO_ES_PATTERN                   One of those in § D6.5
 Reference bandwidth                                      REFBW                              kHz
 Number of epfd↓ points                                Nepfd_DOWN                              –
 Array of Nepfd_DOWN epfd↓ values                     epfd_DOWN[I]                    dB(W/(m2 · BWref))
 Array of Nepfd_DOWN percentages                            PC[I]                             %


D5.1.2 Non-GSO system parameters
The following parameters, as specified in § B3.1 would be used.

                   Parameter description                              Parameter name               Parameter
                                                                                                     units
  Satellite pfd mask                                                 See Part C for definition and format
  Number of non-GSO satellites                                               Nsat                      –
  Transmit centre frequency                                              F_DOWNsat                   MHz
  Exclusion zone angle by latitude, potentially varying by       MIN_EXCLUDE[Latitude]               degree
  satellite
  Minimum satellite tracking duration by latitude                     MIN_DURATION                   second
                                                                         [Latitude]
  Maximum number of satellites operating co-frequency at              MAX_CO_FREQ                      –
  Earth location, potentially varying by latitude                       [Latitude]
  Orbit has repeating ground track maintained by station                  Yes or No                    –
  keeping
  Administration is supplying specific node precession rate               Yes or No                    –
  Station keeping range for ascending node as half total range              Wdelta                   degree
  Minimum operating height                                                 H_MIN                      km
  Minimum operating elevation angle by latitude and                 ES_MINELEV[Latitude]             degree
  azimuth                                                                [Azimuth]
  Minimum angle between active non-GSO satellites at ES             MIN_ANGLE_AT_ES                  degree


For each satellite the following parameters specified in § B3.2 would be used, where the definitions
of the parameters are specified in § D6.3.1 at the time of the start of the simulation.
Note that in the Table below, the indices [N] are present to indicate that there would be a different
value for each satellite, and the N-th value corresponds to the N-th satellite. For the pfd mask
it indicates that the pfd data is structured in such a way that the pfd[N] entry is a reference that points
```


---

## [Página 98]

```text
96                                        Rec. ITU-R S.1503-4

to a particular sub-set. For example each satellite in the constellation could reference the same pfd(lat,
az, el), pfd(lat, X, long), or pfd(lat, , long) table.
                Parameter description                    Parameter name               Parameter units
       pfd mask to use                                         pfd[N]                       –
       Semi-major axis                                          A[N]                        km
       Eccentricity                                             E[N]                        –
       Inclination                                              I[N]                      degree
       Longitude of ascending node                              O[N]                      degree
       Argument of perigee                                      W[N]                      degree
       True anomaly                                             V[N]                      degree


D5.1.3 Run time step parameters
The following run parameters should be calculated using the algorithm in § D4:
                Parameter description                    Parameter name               Parameter units
       Time step                                               TSTEP                         s
       Number of time steps                                   NSTEPS                        –


The time step and statistics take account of the time windows as shown in Fig. 43.

                                                  FIGURE 43
                              Run time, fine and course time steps and time windows




The following are calculated in § D4:
–       Run duration
–       Fine time step
–       Course time step.
In the case that the non-GSO satellite selection method is defined by track duration (e.g. for the
MIN_DURATION [Latitude] to be non-zero for at least one latitude), then it is also necessary to
consider a set of time windows to ensure all combinations of satellite selection are analysed.
The MIN_DURATION is provided for the constellation (possibly dependent upon latitude, which in
this case of the ES). The length of the time window in fine time steps is calculated as the integer ratio
of this track duration divided by fine time step size. The MIN_SLIDING_TIME is also calculated as
an integer number of fine time steps.
```


---

## [Página 99]

```text
                                               Rec. ITU-R S.1503-4                                   97

The minimum sliding time is:
                                                               𝑀𝑖𝑛𝑖𝑚𝑢𝑚𝑂𝑟𝑏𝑖𝑡𝑎𝑙𝑃𝑒𝑟𝑖𝑜𝑑
                    MIN_SLIDING_TIME = max {1 𝑠𝑒𝑐𝑜𝑛𝑑,                                  }
                                                                     100.𝑁𝑠𝑎𝑡𝑒𝑙𝑙𝑖𝑡𝑒𝑠

Where MinimumOrbitalPeriod is the smallest orbit period over all sub-constellations.
The run duration is derived in § D4 so that the constellation has returned to the initial conditions and
hence statistics are complete. This could require that the last time window is only partially included:
i.e. only those time steps within the run duration would be included in the epfd statistics. However
the simulation would continue until the time window is complete to determine the satellites to track.
In addition, as each window starting time is offset it is necessary to run for other windows additional
steps beyond that used by the first-time window.
The length of the sliding window in number of fine time steps is:
        NSW = RoundDown(MIN_DURATION / Tfine)
The number of fine time steps for the MIN_SLIDING_TIME can be calculated as:
        NMSL = RoundUp(MIN_SLIDING_TIME / Tfine)
The number of time windows to use in the calculations is then:
        NTW = RoundUp(Nsw / NMSL)
The number of repeats of the time window for the run duration is then:
        NRepeat = RoundUp(Nstep / NSW)
The total number of fine time steps required to simulate is the run duration plus the time required to
complete all the time windows, namely:
        NTotalSteps = Nrepeat * NSW + (NTW – 1) * NMSL
The total run duration required to get all the sliding windows to complete is then:
        TTotalDuration = NTotalSteps * Tfine
D5.1.4 Algorithms and calculation procedures
In the case that the non-GSO satellite selection method is defined by track duration (e.g. for the
MIN_DURATION [Latitude] to be non-zero for at least one latitude) then the algorithm and
calculation procedures are as in § D5.1.4.2, otherwise they are as in § D5.1.4.1.
The operating non-GSO satellites are those outside the exclusion zone, above their minimum
operating elevation angle and transmitting towards (i.e. height above or equal to
MIN_OPERATING_HEIGHT) the GSO earth station. The maximum number of operating non-GSO
satellites is the maximum number of non-GSO satellites allowed to transmit co-frequency towards
the same area on the ground.
D5.1.4.1 Algorithms and calculation procedures when not using track duration
To calculate epfd↓ values from a non-GSO system that is not using the track duration into one GSO
system earth station the following algorithm should be used. The algorithm can be used on multiple
GSO systems in parallel if required.
Step 1: Read in parameters for non-GSO system as specified in § D5.1.2.
Step 2: Read in GSO parameters as specified in § D5.1.1.
Step 3: If required, calculate the maximum epfd GSO location using the algorithm in § D3.1
        otherwise use the GSO satellite and ES location requested.
```


---

## [Página 100]

```text
98                                     Rec. ITU-R S.1503-4

Step 4: Calculate the number of time steps and time step size using algorithm in § D4 and hence
        calculate end time.
Step 4bis: Initialize statistics by zeroing all bins of epfd↓ values.
Step 5: If a dual time step algorithm is included then use Sub-step 5.1, otherwise Ncoarse = 1 all the
        time.
        Sub-step 5.1: Calculate coarse step size Tcoarse = Tfine * Ncoarse.
Step 6: If a dual time step algorithm is included then repeat Sub-step 6.1 to Step 22 until end time is
        reached, otherwise repeat Steps 7 to 22 until end time is reached.
        Sub-step 6.1: If it is the first time step then set Tstep = Tfine.
        Sub-step 6.2: Otherwise if there are less than Ncoarse steps remaining then set Tstep = Tfine.
         Sub-step 6.3: Otherwise, if any of the GRX() for the last time step were within 30 dB of
         peak then set Tstep = Tfine; otherwise, set Tstep = Tcoarse.
Step 7: Update position vectors of all earth stations based on coordinate system in § D6.1.
Step 8: Update position vectors of all GSO satellites based on coordinate system in § D6.2.
Step 9:      Update position and velocity vectors of all non-GSO satellites based on coordinate
             system, orbit prediction model and station keeping algorithm in § D6.3.
Step 10:     Set epfd↓ = 0.
Step 11:     Select all non-GSO satellites visible to the GSO earth station using the algorithm in
             § D6.4.1.
Step 12:     Repeat Steps 13 to 18 for each visible non-GSO satellite.
Step 13:    Calculate the parameters required by the pfd mask, either (lat, , long) or (lat, azimuth,
            elevation) as required, using the definition of angles in § D6.4.4 or § D6.4.5.
Step 13bis: Calculate the (AzimuthNGSO, NGSO) of the non-GSO satellite as seen at the GSO ES
            location using the definition of angles in § D6.4.4.
Step 14:    Using the pfd mask for the selected non-GSO satellite, calculate pfd(lat, , long) or
            pfd(lat, azimuth, elevation) at the GSO earth station using the non-GSO satellite pfd
            mask as specified in § D5.1.5.
Step 15:    Calculate the off-axis angle  at GSO earth station between line to the GSO satellite and
            the non-GSO satellite.
Step 16:    Calculate GRX() = Receive gain (dB) at GSO earth station using relevant gain pattern
            specified in algorithms in § D6.5.
Step 17:    Calculate epfd↓i for this non-GSO satellite using:
                epfd↓i = pfd + GRX() – Gmax
            where Gmax is the peak gain of the GSO earth station antenna.
Step 18:    Store the epfd↓i entries for each satellite that:
            •   either has an  ≥ 0[latitude] for that satellite and has an NGSO ≥
                0[latitude][AzimuthNGSO]
            • or for which GRX() > min[Gmax − 30 dB, GRX(o[Latitude]])
Step 19:    Repeat Steps 20 and 21 for each time step as long as no more than MAX_CO_FREQ[lat]
            satellites are included in Step 23.
            MAX_CO_FREQ[lat] is the maximum number of operating non-GSO satellites at the
            latitude of GSO_ES considered corresponding to the maximum number of satellites
            allowed to transmit at the same frequency towards the same area on the ground, fulfilling
```


---

## [Página 101]

```text
                                        Rec. ITU-R S.1503-4                                           99

            the GSO exclusion zone, the minimum elevation angle and the MIN_ANGLE_AT_ES
            requirements as defined for the non-GSO system.
Step 20:    Apply Step 23 for the satellite which has an  ≥ 0[latitude] and an NGSO ≥
            0[latitude][AzimuthNGSO] and with the highest single epfd↓i.
Step 21:    If the MIN_ANGLE_AT_ES between non-GSO satellites has been specified, then
            remove the non-GSO satellites from those in Step 18 which have an  ≥ 0[latitude] and
            an NGSO ≥ 0[latitude][AzimuthNGSO] that do not meet this criterion with respect to the
            satellite used in Step 20.
Step 22:    Repeat Step 23 for those satellites for which GRX() > min[Gmax − 30 dB,
            GRX(o[Latitude]]).
Note:       There should be no double-counting of satellites in Step 23. In particular, if a satellite is
            on the list of the highest MAX_CO_FREQ[lat] satellites and also for which GRX() >
            min[Gmax − 30 dB, GRX(o[Latitude]]) it should be included only once as part of the
            MAX_CO_FREQ[lat] satellites in Step 20.
Step 23:    Increment epfd↓ by the epfd↓i value in linear.
Step 24:    Increment epfd↓ statistics by epfd↓ for this time step by (Tstep/Tfine) entries.
Step 25:    Generate the epfd↓ CDF from the epfd↓ PDF using the algorithm in § D7.1.2.
Step 26:    Compare epfd↓ statistics with limits using the algorithm in § D7.1.
Step 27:    Output results in format specified in § D7.3.
D5.1.4.2 Algorithms and calculation procedures when using track duration
To calculate epfd↓ values from a non-GSO system that is using the track duration into one GSO
system earth station the following algorithm should be used. The algorithm can be used on multiple
GSO systems in parallel if required.
Step 1:     Read in parameters for non-GSO system as specified in § D5.1.2.
Step 2:     Read in GSO parameters as specified in § D5.1.1.
Step 3:     If required, calculate the maximum epfd GSO location using the algorithm in § D3.1
            otherwise use the GSO satellite and ES location requested.
Step 4:     Calculate the number of time steps and time step size using algorithm in § D4 and hence
            calculate end time. As described in § D5.1.3, adjust the MIN_SLIDING_TIME and
            MIN_DURATION to be an integer number of fine time step and hence calculate the
            NUM_SLIDE_WINDOWS. Increment the run duration by the integer number of fine
            time steps so that all sliding windows will have complete statistics.
Step 4bis: Initialize statistics by zeroing all bins of epfd↓ values for each of the NUM_SLIDE
            WINDOWS.
Step 5:     If a dual time step algorithm is included then use Sub-step 5.1, otherwise Ncoarse = 1 all
            the time.
            Sub-step 5.1:      Calculate coarse step size Tcoarse = Tfine * Ncoarse.
Step 6:     If a dual time step algorithm is included then repeat Sub-step 6.1 to Step 22 until end
            time is reached, otherwise repeat Steps 7 to 22 until end time is reached.
            Sub-step 6.1:      If it is the first time step then set Tstep = Tfine.
            Sub-step 6.2:      Otherwise if there are less than Ncoarse steps remaining then set
                               Tstep = Tfine.
```


---

## [Página 102]

```text
100                                         Rec. ITU-R S.1503-4

              Sub-step 6.3:     Otherwise, if any of the GRX() for the last time step were within 30 dB
                                of peak then set Tstep = Tfine; otherwise, set Tstep = Tcoarse.
Step 7:       Update position vectors of all earth stations based on coordinate system in § D6.1.
Step 8:       Update position vectors of all GSO satellites based on coordinate system in § D6.2.
Step 9:       Update position and velocity vectors of all non-GSO satellites based on coordinate
              system, orbit prediction model and station keeping algorithm in § D6.3.
Step 10:      Set epfd↓ = 0.
Step 11:      Select all non-GSO satellites visible to the GSO earth station using the algorithm in
              § D6.4.1.
Step 12:      Repeat Steps 13 to 18 for each visible non-GSO satellite.
Step 13:      Calculate the parameters required by the pfd mask, either (lat, , long) or (lat, azimuth,
              elevation) as required, using the definition of angles in § D6.4.4 or § D6.4.5.
Step 13bis: Calculate the (AzimuthNGSO, NGSO) of the non-GSO satellite as seen at the GSO ES
            location using the definition of angles in § D6.4.4.
Step 14:      Using the pfd mask for the selected non-GSO satellite, calculate pfd(lat, , long) or
              pfd(lat, azimuth, elevation) at the GSO earth station using the non-GSO satellite pfd
              mask as specified in § D5.1.5.
Step 15:      Calculate the off-axis angle  at GSO earth station between line to the GSO satellite and
              the non-GSO satellite.
Step 16:      Calculate GRX() = Receive gain (dB) at GSO earth station using relevant gain pattern
              specified in algorithms in § D6.5.
Step 17:      Calculate epfd↓i for this non-GSO satellite using:
              epfd↓i = pfd + GRX() – Gmax
              where Gmax is the peak gain of the GSO earth station antenna.
Step 18:      Store the epfd↓i entries for each satellite that:
              •   either has an  ≥ 0[latitude] for that satellite and has an NGSO ≥
                  0[latitude][AzimuthNGSO]
              •   or for which GRX() > min[Gmax − 30 dB, GRX(0[Latitude]])
Step 19:      If a window is closing this time step then identify which non-GSO satellites met the 0,
              0 constraints for the full duration of the window.
Step 19bis: For each of those satellites that met the 0, 0 constraints for the full duration of the
            window, calculate the highest epfd↓[nSat] over the time window and sort this list of
            satellites by maximum epfd↓[nSat] per satellite.
Step 20:    Repeat Steps 21 and 22 for each time step in the window for the MAX_CO_FREQ[lat]
            satellite’s epfd↓[nSat] contributions on this list plus those satellites for which GRX() >
            min[Gmax − 30 dB, GRX(0[Latitude]]), where MAX_CO_FREQ[lat] is the maximum
            number of operating non-GSO satellites at the latitude of GSO_ES considered
            corresponding to the maximum number of satellites allowed to transmit at the same
            frequency towards the same area on the ground, fulfilling the GSO exclusion zone and
            minimum elevation angle requirements as defined by for the non-GSO system.
Note: There should be no double-counting of satellites in Step 21. In particular, if a satellite is on the list of
the highest MAX_CO_FREQ[lat] satellites and also for which GRX() > min[Gmax − 30 dB, GRX(0[Latitude]])
it should be included only once as part of the MAX_CO_FREQ[lat] satellites in Step 21.
Step 21:      Increment epfd↓ by the epfd↓i value in linear.
```


---

## [Página 103]

```text
                                          Rec. ITU-R S.1503-4                                          101

Step 22:      Increment epfd↓ statistics for the relevant slide window by epfd↓ for this time step by
               (Tstep/Tfine) entries. If last time step was a course time step and the window closed during
               that time step, then the epfd↓ statistics should be updated by that part of Tstep that was in
               the window and the remaining part stored for the following window. If the run since
               start time for this slide window exceeds the run duration then only include that part of
               the time window that is within the run duration in the statistics.
Step 23:      Generate the epfd↓ CDF for all slide windows from the epfd↓ PDF using the algorithm
              in § D7.1.2.
Step 24:      Compare epfd↓ statistics for all slide windows with limits using the algorithm in § D7.1.
Step 25:      Output results in format specified in § D7.3.
D5.1.5 pfd mask calculation
The pfd mask is defined as a table of pfd values for various angles and latitudes.
Note that the latitude range should be:
             Minimum:          –i
             Maximum:          +i
where i is the inclination of the non-GSO satellite’s orbit.
In general, the (azimuth, elevation) or (, long) angles calculated at each time step will be between
two values in the arrays. In this case bilinear interpolation between pfd values should be used with
equations:
           𝑝𝑓𝑑 = (1 − λ𝑥 )(1 − λ𝑦 )𝑝𝑓𝑑11 + λ𝑥 (1 − λ𝑦 )𝑝𝑓𝑑21 + (1 − λ𝑥 )λ𝑦 𝑝𝑓𝑑12 + λ𝑥 λ𝑦 𝑝𝑓𝑑22
where:
                                                       𝑥−𝑥
                                               λ𝑥 = 𝑥 − 𝑥1
                                                       2     1
                                                      𝑦 − 𝑦1
                                               λ𝑦 = 𝑦 − 𝑦
                                                       2     1

And (x, y) are the two dimensions of the pfd mask.
If the angles are outside the pfd mask, the software calculates pfd from the highest angle in the mask
(i.e. at the mask edge).
The mask that is closer in latitude to that of the reference satellite should be used. Part C gives more
information about the format and sampling of the pfd mask.
D5.1.6 Outputs
The result of the algorithm is two arrays in format:

                    Array of epfd↓              epfd_DOWN_CALC[I]            dB(W/(m2 · BWref))
                        values
                      Array of                       PC_CALC[I]                      %
                     percentages


where PC_CALC[I] is the percentage of time epfd_DOWN_CALC[I] is exceeded.
```


---

## [Página 104]

```text
102                                      Rec. ITU-R S.1503-4

D5.2    epfd↑ software description
This section describes the algorithm to calculate epfd↑ from non-GSO earth stations into a GSO
uplink. The locations of the ES can be defined in one of two ways:
1)      It is assumed that the Earth is populated with a uniform distribution of non-GSO earth
        stations. In this case the ES_ID in the e.i.r.p. mask should be set to −1.
2)      The locations of specific ES are used via a field in the ES e.i.r.p. mask. In this case the density
        field is not used.
Each earth station points towards a non-GSO satellite using pointing rules for that constellation,
and transmits with a defined e.i.r.p. From the e.i.r.p. and off-axis gain pattern for each earth station,
the epfd↑ at the GSO can be calculated. This is repeated for a series of time steps until a distribution
of epfd↑ is produced. This distribution can then be compared with the limits to give a go/no go
decision.
Figure 44 shows the geometry with population of non-GSO earth stations transmitting to
a constellation of non-GSO satellites, together with a test GSO satellite receiving from a GSO earth
station.

                                                FIGURE 44
                                        Example of epfd(up) scenario




D5.2.1 Configuration parameters
This sub-section specifies the parameters required for all epfd↑ calculations defined in the RR.
This would be a data-set of N sets of limits that can be shared between runs. The Table could be
queried so that the required values can be used depending upon non-GSO system frequency.
```


---

## [Página 105]

```text
                                         Rec. ITU-R S.1503-4                                          103

For each set of limits the following would be defined as generated by in § D2.1.

             Parameter name                         Parameter value      Parameter units and ranges
 Frequency                                              FREQ                         MHz
 GSO gain pattern                                     FEND_UP                One of those in § D6.5
 GSO peak gain                                 GSO_SAT_PEAKGAIN                       dBi
 GSO half power beamwidth                     GSO_SAT_BEAMWIDTH                     degree
 Reference bandwidth                                   RAFBW                         kHz
 Number of epfd↑ points                               Nepfd_UP                         –
 Array of Nepfd_UP epfd↑ values                       epfd_UP[I]              dB(W/(m2 · BWref))
 Array of Nepfd_UP percentages                         PC_UP[I]                       %


D5.2.2 Determination of maximum epfd configuration
The maximum epfd location of the GSO satellite and beam centre is defined in § D3.2.
D5.2.3 Calculation of run steps
The time step and number of time steps are calculated using the algorithm in § D4 which also
describes the optional dual time step option.
D5.2.4 Inputs
D5.2.4.1 Input parameters
This section defines the input parameters for a particular non-GSO system scenario. In this case, input
is a generic term that could include files or user input. Information is required for:
–        non-GSO system;
–        GSO system;
–        run configuration.
D5.2.4.2 Non-GSO system parameters
The following parameters, as specified in § B3.1 would be used:

             Parameter description                     Parameter name            Parameter units
 Number of non-GSO satellites                                Nsat                          –
 Orbit has repeating ground track maintained by           Yes or No                        –
 station keeping
 Administration is supplying specific node                Yes or No                        –
 precession rate
 Station keeping range for ascending node as half            Wdelta                  degrees
 total range


For each satellite the following parameters specified in § B3.2, would be used, where the definitions
of the parameters are specified in § D6.3.1 at the time of the start of the simulation.
Note that in the Table below, the indices [N] are present to indicate that there would be a different
value for each satellite, and the N-th value corresponds to the N-th satellite.
```


---

## [Página 106]

```text
104                                        Rec. ITU-R S.1503-4



              Parameter description                     Parameter name        Parameter units
Semi-major axis                                               A[N]                   km
Eccentricity                                                  E[N]                    –
Inclination                                                   I[N]                 degrees
Longitude of ascending node                                   O[N]                 degrees
Argument of perigee                                          W[N]                  degrees
True anomaly                                                  V[N]                 degrees


Each satellite must have an independent set of six orbital parameters for orbit definition and
subsequent propagation.
To define the characteristics of non-GSO earth stations, the following parameters, as specified in
§ B4.2 would be used:

               Parameter description                     Parameter name        Parameter units
 Maximum number of co-frequency tracked non-         MAX_CO_FREQ [Latitude]           –
 GSO satellites
 Earth station e.i.r.p. mask by latitude                  ES_e.i.r.p.[lat]       dB(W/BWref)
 Minimum elevation angle                              ES_MINELEV[Latitude]          degree
                                                           [Azimuth]
 Exclusion zone angle by latitude, potentially           MIN_EXCLUDE                degree
 varying by satellite                                         [Latitude]
 Average number of non-GSO ES active at the               ES_DENSITY                 km2
 same time per km2
 Average distance between cell or beam foot-             ES_DISTANCE                 km
 print centres
 The maximum number of non-geostationary              MAX_CO_FREQ_SAT                 –
 earth stations tracked co-frequency by a non-
 geostationary satellite within the EPFD(up)
 calculation. If a value is not provided, it is
 assumed that the maximum number of earth
 stations tracked co-frequency by a non-
 geostationary satellite is equal to the number of
 earth stations created for the epfd↑ run
 The minimum angle in degrees at the non-GSO MIN_ANGLE_AT_SAT                       degree
 satellite between the lines to any two active non-
 GSO earth stations. Assumed to be zero if not
 provided
 Minimum angle in degrees at the surface of the MIN_ANGLE_AT_ES                     degree
 Earth between the lines to any two active non-
 GSO satellites. Assumed to be zero if not
 provided.    Not      applicable     if    the
 MIN_DURATION[Latitude] is non-zero


Note that the minimum track duration is not used for the epfd(up) case.
```


---

## [Página 107]

```text
                                       Rec. ITU-R S.1503-4                                        105

D5.2.4.3 GSO system parameters
The GSO system can be either calculated or use worst-case parameters using the algorithm in § D3.2
or entered values. The required parameters as specified are:

              Parameter description                 Parameter name              Parameter units
 GSO satellite longitude                            GSO_SAT_LONG                     degree
 GSO boresight latitude                                  BS_LAT                      degree
 GSO boresight longitude                                BS_LONG                      degree
 GSO reference gain pattern                       GSO_SAT_PATTERN            One of those in § D6.5


These parameters are defined in §§ D6.1 and D6.2.
D5.2.4.4 Run parameters
The following run parameters should be calculated using the algorithm in § D4:

             Parameter description                Parameter name               Parameter units
 Time step                                             TSTEP                             s
 Number of time steps                                 NSTEPS                             –


D5.2.5 Production of non-GSO earth station distribution
In the case that the non-GSO ES locations are defined by a distribution, the following method should
be used:
Step 1: Calculate the number of actual operating non-GSO earth stations that the representative earth
         station will represent using:
                 NUM_ES = ES_DISTANCE * ES_DISTANCE * ES_DENSITY
Step 2: Calculate e.i.r.p. to use for each representative non-GSO earth station using:
                           REP_e.i.r.p. = ES_e.i.r.p. + 10log10(NUM_ES)
Step 3: Define the GSO service area as the region enclosed by the contour representing a relative
        gain of 15 dB.
Step 4: For every distance ES_DISTANCE in latitude and distance ES_DISTANCE in longitude
        within the service area defined in Step 3, locate a representative non-GSO earth station with
        radiating with REP_e.i.r.p.
If the supplied ES_DISTANCE is zero, then in Step 1 set NUM_ES = 1 and at Step 4 locate a single
non_GSO ES at the boresight of the GSO satellite.
The NUM_ES is typically 1 for TDMA and FDMA systems and for CDMA systems equal to the
number of co-frequency ES all operating on the same frequency at the same time and location. The
ES_DISTANCE relates to the average distance between co-frequency beams.
The deployment method should be symmetric around the (latitude, longitude) of the GSO satellite’s
boresight, as shown in Fig. 45.
```


---

## [Página 108]

```text
106                                     Rec. ITU-R S.1503-4

                                               FIGURE 45
                               Deployment method for non-GSO earth stations




No non-GSO ESs should be deployed below the minimum latitude or above the maximum latitude,
where these two extreme values are derived using the methodology in § D3.2.3.
The separation in latitude in radians between non-GSO ES can be calculated from the distance using:
                                                           𝑑
                                              ∆𝑙𝑎𝑡 = 𝑅
                                                           𝑒

The separation in longitude in radians between non-GSO ES can be calculated using:
                                                           𝑑
                                          ∆𝑙𝑜𝑛𝑔 = 𝑅 cos 𝑙𝑎𝑡
                                                       𝑒


D5.2.6 Algorithms and calculation procedures
To calculate epfd↑ values from one non-GSO system into one GSO system satellite the following
algorithm should be used. The algorithm can be used on multiple GSO systems in parallel if required:
Step 1: Read in parameters for non-GSO system as specified in § D5.2.4.2.
Step 2: Read in GSO parameters as specified in § D5.2.4.3.
Step 3: If required calculate maximum epfd GSO location using the algorithm in § D3.2 otherwise
        use the GSO satellite and ES location requested.
Step 4: If required calculate locations of non-GSO earth stations using the algorithm in § D5.2.5.
Step 5: Initialize statistics by zeroing all bins of epfd↑ values.
Step 6: If required calculate number of time steps and time step size using the algorithm in § D4 and
        hence calculate end time.
        If a dual time step algorithm is included then use Sub-step 6.1, otherwise Ncoarse = 1 all the
        time.
```


---

## [Página 109]

```text
                                        Rec. ITU-R S.1503-4                                          107

        Sub-step 6.1: Calculate coarse step size Tcoarse = Tfine * Ncoarse.
Step 7: Repeat Steps 8 to 24 for all time steps.
        If a dual time step algorithm is included, then repeat Sub-step 7.1 to Step 22 until end time is
        reached.
        Sub-step 7.1: If it is the first-time step then set Tstep = Tfine.
        Sub-step 7.2: Otherwise if there are less than Ncoarse steps remaining then set Tstep = Tfine.
         Sub-step 7.3: Otherwise if any of the  angles for the last time step were within coarse of the
         exclusion zone angle then set the Tstep = Tfine; otherwise, use Tstep = Tcoarse.
Step 8: Update position vectors of all earth stations using algorithm in § D6.1.
Step 9: Update position and velocity vectors of all non-GSO satellites using algorithm in § D6.3.2.
Step 10:     Update position vectors of GSO satellite using algorithm in § D6.2.
Step 11:     Initialise a list of possible links comprising triples of {non-GSO satellite, non-GSO ES,
             epfd↑I } to be an empty list.
Step 12:     Set the counter of times each satellite is used to zero for all satellites.
Step 13:     Set the counter of times each non-GSO ES is used to zero for all non-GSO ES.
Step 14:     Repeat Steps 15 to 24 for all non-GSO earth stations.
Step 15:     Determine if this non-GSO earth station is visible to the GSO satellite using the algorithm
             in § D6.4.1.
Step 16:    If the non-GSO earth station is visible to the GSO satellite then do Steps 17 to 24.
Step 17:    For all non-GSO satellites repeat Steps 18 to 24.
Step 18:    If this non-GSO satellite is a) visible to the non-GSO ES and b) above the minimum
            elevation angle 0[latitude][AzimuthNGSO] at the non-GSO for this latitude and the non-
            GSO satellite’s azimuth and c) outside the exclusion zone for this latitude 0[latitude],
            undertake Steps 19 to 24.
Step 19:    Calculate ES_e.i.r.p.[lat] (dB(W/BWraf)) of non-GSO earth station at its given latitude in
            direction of GSO satellite using non-GSO earth station e.i.r.p. mask in § C3.
                 REP_e.i.r.p. = ES_e.i.r.p.[lat, off-axis angle] + 10log10 (NUM_ES)
Step 20:    Calculate GRX = receive relative gain (dB) at GSO satellite using relevant gain pattern
            specified in the algorithms in § D6.5.
Step 21:    Calculate D = distance (km) between the non-GSO earth station and the GSO satellite
            using the algorithm in § D6.4.1.
Step 22:    Calculate the spreading factor LFS = 10log(4 D2) + 60.
Step 23:    Calculate epfd↑i for this non-GSO satellite:
                               epfd↑i = REP_e.i.r.p. – LFS + GRX – Gmax
Step 24:    Add to the list of possible links this {non-GSO Satellite, non-GSO ES, epfd↑i }.
Step 25:    Sort the list of possible links by epfd↑i with the highest epfd↑i at the top.
Step 26:    Set epfd↑ = 0.
```


---

## [Página 110]

```text
108                                         Rec. ITU-R S.1503-4

Step 27:      If the list of possible links is empty jump to Step 35 otherwise repeat Steps 28 to 34 for
              the link {non-GSO satellite, non-GSO ES, epfd↑i } on the list of possible link with the
              highest epfd↑i
Step 28:      Increment the epfd↑ by this link’s epfd↑i
Step 29:      Increment the counter of the number of times this link’s non-GSO satellite has been used.
Step 30:      If this non-GSO satellite counter is equal to MAX_CO_FREQ_SAT then remove all
              remaining links on the list of possible links that use this non-GSO satellite.
Step 31:      Increment the counter of the number of times this link’s non-GSO ES has been used.
Step 32:      If this non-GSO ES counter is equal to MAX_CO_FREQ then remove all remaining links
              on the list of possible links that use this non-GSO ES.
Step 33:      If the MIN_ANGLE_AT_ES is non-zero, then remove all links from the list that have an
              angle at the ES to this link’s non-GSO satellite is less than MIN_ANGLE_AT_ES.
Step 34:      If the MIN_ANGLE_AT_SAT is non-zero, then remove all links from the list that have
              an angle at the satellite to this link’s non-GSO satellite is less than
              MIN_ANGLE_AT_SAT.
Step 35:      Increment epfd↑ statistics by this epfd↑.
              If a dual time step algorithm is included, then the step below should be used:
              Sub-step 35.1:    Increment epfd↑ statistics by the epfd↑ for this time step by Tstep/Tfine
                                entries.
Step 36:      Generate the epfd↑ CDF from the epfd↑ pdf using the algorithm in § D7.1.2.
Step 37:      Compare epfd↑ statistics with limits using the algorithm in § D7.1.
Step 38:      Output results in format specified in § D7.2.
D5.2.7 Calculation of e.i.r.p.
If the ES_e.i.r.p. mask is of the form ES_e.i.r.p.(lat)() then find the table with the nearest latitude to
the non-GSO ES under consideration, and then use that to undertake linear interpolation in ES_e.i.r.p.
against the off-axis angle at the non-GSO ES towards the GSO satellite.
If the ES_e.i.r.p. mask is of the form ES_e.i.r.p.(lat)(az)(el)(DeltaLongES) then find the table with
the nearest latitude to the non-GSO ES under consideration. Within that table, find the (az)(el) array
with the smallest angular offset to the (azimuth, elevation) of the non-GSO satellite as seen by the
non-GSO ES. Use that array to undertake linear interpolation in ES_e.i.r.p. against the difference in
longitude between the non-GSO ES and the GSO satellite.
D5.2.8 Outputs
The result of the algorithm is two arrays of size Nepfd↑ in format:

           Array of Nepfd_UP epfd↑ values           epfd_UP_CALC[I]         dB(W/(m2 · BWref))
           Array of Nepfd_UP percentages              PC_CALC[I]                     %


where PC_CALC[I] is the percentage of time epfd_UP_CALC[I] is exceeded.

D5.3       epfdis software description
This section describes the algorithm to calculate epfdis from non-GSO space stations into a GSO
uplink. From the e.i.r.p. and off-axis angle for each space station, the epfdis at the GSO space station
```


---

## [Página 111]

```text
                                         Rec. ITU-R S.1503-4                                          109

can be calculated. This is repeated for a series of time steps until a distribution of epfdis is produced.
This distribution can then be compared with the limits to give a go/no go decision.
D5.3.1 Configuration parameters
This sub-section specifies the parameters required for all epfdis calculations. This would be a data-set
of N sets of limits that can be shared between runs. The Table could be queried so that the required
values can be used depending upon non-GSO system frequency.
For each set of limits the following would be defined as derived in § D2.1.

               Parameter name                        Parameter value            Parameter units and
                                                                                     ranges
 Frequency band start                                      FREQ                         MHz
 GSO gain pattern                                        FEND_IS                One of those in § D5.5
 GSO peak gain                                    GSO_SAT_PEAKGAIN                       dBi
 GSO half power beamwidth                         GSO_SAT_BEAMWIDT                     degrees
                                                          H
 Reference bandwidth                                      RIFBW                          kHz
 Number of epfdis points                                 Nepfd_IS                         –
 Array of Nepfd_IS epfdis values                         epfd_IS[I]              dB(W/(m2 · BWrif))
 Array of Nepfd_IS percentages                           PC_IS[I]                         %


D5.3.2 Determination of maximum epfd configuration
The maximum epfd location of GSO satellite and beam centre is defined in § D3.3.
D5.3.3 Calculation of run steps
A single time step and number of time steps are calculated using the algorithm in § D4.
D5.3.4 Input parameters
This sub-section defines the input parameters for a particular non-GSO system scenario. In this case,
input is a generic term that could include files or user input. Information is required for:
–        non-GSO system;
–        GSO system;
–        run configuration.
D5.3.4.1 Non-GSO system parameters
The following parameters, as specified in § B2.1 would be used:
            Parameter description                    Parameter name                Parameter units
 Number of non-GSO satellites                               Nsat                          –
 Orbit has repeating ground track maintained by          Yes or No                        –
 station keeping
 Administration is supplying specific node               Yes or No                        –
 precession rate
 Station keeping range for ascending node as               Wdelta                      degrees
 half total range
```


---

## [Página 112]

```text
110                                        Rec. ITU-R S.1503-4

For each satellite the following parameters specified in § B2.1 would be used, where the definitions
of the parameters are specified in § D6.3.1 at the time of the start of the simulation.
Note that in the Table below, the indices [N] are present to indicate that there would be a different
value for each satellite, and the N-th value corresponds to the N-th satellite.

               Parameter description                    Parameter name                 Parameter units
 Semi-major axis                                               A[N]                           km
 Eccentricity                                                  E[N]                            –
 Inclination                                                   I[N]                         degrees
 Longitude of ascending node                                   O[N]                         degrees
 Argument of perigee                                           W[N]                         degrees
 True anomaly                                                  V[N]                         degrees


Each satellite must have an independent set of six orbital parameters for orbit definition and
subsequent propagation.
To define the characteristics of non-GSO space stations, the following parameters, as specified in
§ B4.3 would be used:

               Parameter description                    Parameter name                 Parameter units
 e.i.r.p. per space station by latitude                     non-                         dB(W/BWrif)
                                                     GSO_SS_e.i.r.p.[Lat][]
 Minimum transmit frequency(1)                                 IS_F                          GHz
(1)
      The filing administration can supply a set of space station e.i.r.p. masks and associated frequency range
      for which the mask is valid.


D5.3.4.2 GSO system parameters
The GSO system can either use worst-case parameters using the algorithm in § D5.2 or entered values.
The required parameters are:

               Parameter description                    Parameter name                 Parameter units
 GSO satellite longitude                                GSO_SAT_LONG                        degrees
 GSO boresight latitude                                      BS_LAT                         degrees
 GSO boresight longitude                                    BS_LONG                         degrees
 GSO reference gain pattern                           GSO_SAT_PATTERN               One of those in § D5.5


These parameters are defined in §§ D6.1 and D6.2.
D5.3.4.3 Run parameters
The following run parameters should be calculated using the algorithm in § D4:

               Parameter description                    Parameter name                 Parameter units
 Time step                                                    TSTEP                            s
 Number of time steps                                        NSTEPS                            –
```


---

## [Página 113]

```text
                                         Rec. ITU-R S.1503-4                                            111

D5.3.5 Algorithms and calculation procedures
In the calculation of the dual time step for the epfdis computation, Ncoarse = 1.
To calculate epfdis values from one non-GSO system into one GSO system satellite the following
algorithm should be used. The algorithm can be used on multiple GSO systems in parallel if required:
Step 1: Read in parameters for non-GSO system as specified in § D5.3.4.2.
Step 2: Read in GSO parameters as specified in § D5.3.4.3.
Step 3: If required calculate worst-case GSO location using algorithm in § D3.3.
Step 4: Initialize statistics by zeroing all bins of epfdis values.
Step 5: If required calculate number of time steps and time step size using algorithm in § D4 and
         hence calculate end time.
         If a dual time step algorithm is included then use Sub-step 5.1, otherwise Ncoarse = 1 all the
         time.
         Sub-step 5.1: Calculate coarse step size Tcoarse = Tfine * Ncoarse.
Step 6: Repeat Steps 7 to 19 for all time steps.
         If a dual time step algorithm is included, then repeat Sub-step 6.1 to Step 17 until end time
         is reached.
         Sub-step 6.1: If it is the first-time step then set Tstep = Tfine.
         Sub-step 6.2: Otherwise if there are less than Ncoarse steps remaining then set Tstep = Tfine.
         Sub-step 6.3: Otherwise, if any of the α angles for the last time step were within coarse of the
         exclusion zone angle then set the Tstep = Tfine; otherwise, use Tstep = Tcoarse.
Step 7: Update position and velocity vectors of all non-GSO satellites using algorithm in § D6.3.
Step 8: Update position vectors of GSO satellite using algorithm in § D6.2.
Step 9: Set epfdis = 0.
Step 10: Repeat Steps 10 to 18 for all non-GSO space stations.
Step 11: Determine if this non-GSO space station is visible to the GSO satellite using the algorithm
         in § D6.4.1.
Step 12: If the non-GSO space station is visible to the GSO satellite then do Steps 13 to 18.
Step 13: Calculate e.i.r.p. (dB(W/BWrif)) of non-GSO space station in direction of GSO satellite using
         the e.i.r.p. mask in § C3 for the non-GSO space station’s latitude.
Step 14: Calculate GRX = receive relative gain (dB) at GSO satellite using relevant gain pattern
         specified in algorithms in § D6.5.
Step 15: Calculate D = distance (km) between the non-GSO space station and the GSO satellite using
         the algorithm in § D6.4.1.
Step 16: Calculate the spreading factor LFS = 10 log(4 D2) + 60.
Step 17: Calculate epfdisi for this non-GSO satellite:
                                  epfdisi = e.i.r.p. – LFS + GRX – Gmax
Step 18: Increment epfdis by epfdisi.
         Sub-step 19: Increment epfdis statistics by this epfdis.
         If a dual time step algorithm is included then the step below should be used:
         Sub-step 19.1: Increment epfdis statistics by the epfdis for this time step by Tstep/Tfine entries.
Step 20: Generate the epfdis CDF from the epfdis pdf using the algorithm in § D7.1.2.
```


---

## [Página 114]

```text
112                                        Rec. ITU-R S.1503-4

Step 21: Compare epfdis statistics with limits using algorithm in § D7.1.
Step 22: Output results in format specified in § D7.2.
D5.3.6 Outputs
The result of the algorithm is two arrays in format:

         Array of Nepfd_IS epfdis values              epfd_IS_CALC[I]       dB(W/(m2 · BWrif))
         Array of Nepfd_IS percentages                   PC_CALC[I]                 %


where PC_CALC[I] is percentage of time epfd_IS_CALC[I] is exceeded.


D6      Geometry and algorithms
This section describes the geometry that defines the core algorithms used in the software. One aspect
is the conversion into a generic cartesian vector-based coordinate system. The precise orientation of
the X vector is not specified in this Recommendation to allow alternative implementations by
developers. The axis chosen should not impact the results as satellite and Earth coordinates are
defined relative to the Earth.
To aid developers examples coordinate systems are used to show how to convert to and from generic
vectors.

D6.1    Earth coordinates system
Figure 46 shows the reference coordinate system for earth stations.

                                                 FIGURE 46
                                             Definition of latitude




The Earth is defined as a sphere with radius as specified in § A2.2 = Re. The Earth rotates around an
axis, the Z axis, at a rate defined in § A2.2 = e. Perpendicular to the Z axis, crossing the Earth at the
Equator, is the XY plane.
Earth stations are located on this sphere based upon two angles:
Latitude: angle between line from centre of Earth to earth station and XY plane;
Longitude: angle as shown in Fig. 47.
```


---

## [Página 115]

```text
                                         Rec. ITU-R S.1503-4                                          113

                                                FIGURE 47
                                           Definition of longitude




Earth stations are assumed to have constant (latitude, longitude) positions in time.
The orientation within the XY plane of the X and Y axes is not specified in this Recommendation as
all locations are referenced to the Earth rather than one particular inertial frame. This allows different
implementations to use different reference points if required without impacting on the results.
One possible implementation is what is described as the geocentric inertial system. For this example
case, conversion from geographic coordinates is achieved using:
                                                        𝑥
                                  Long = arccos (               )       if y  0                      (5)
                                                    √𝑥 2 +𝑦 2

                                                        𝑥
                                 Long =– arccos (                )          if y  0                  (6)
                                                     √𝑥 2 +𝑦 2

                                                                 𝑧
                                         Lat = arctan (                 )                             (7)
                                                            √𝑥 2 +𝑦 2


If this example coordinate system is used, then the conversion from geographic coordinates into
geocentric inertial system coordinates is:
                                        x = Re cos(lat) cos(long)                                     (8)

                                        y = Re cos(lat) sin(long)                                     (9)

                                              z = Re sin(lat)                                        (10)
where:
          (x, y, z):   coordinates in the geocentric inertial system
              long:    geographic longitude
                lat:   geographic latitude.
In this example geocentric inertial reference system, the equation for motion of a mass point on the
Earth’s surface would be as:
                                  𝑥     𝑅𝑒 cos(lat) cos (lon + Ω𝑒 𝑡)
                                 [𝑦] = [ 𝑅𝑒 cos(lat) sin (lon + Ω𝑒 𝑡) ]                              (11)
                                  𝑧      𝑅𝑒 sin(lat)
where:
                lat:   geographic latitude of the mass point on the Earth’s surface
```


---

## [Página 116]

```text
114                                     Rec. ITU-R S.1503-4

              lon:   geographic longitude of the mass point on the Earth’s surface
                t:   time
              e:    angular rate of rotation of the Earth.

D6.2    GSO satellite coordinate system
The geostationary arc is a circle in the XY plane at a distance of Rgeo from the Earth’s centre where
Rgeo is specified in § A2.2. Individual geostationary satellites have location on this circle defined by
a longitude as shown in Fig. 48.

                                                FIGURE 48
                                    Definition of GSO satellite longitude




Geostationary satellites are assumed to have constant longitude in time. The conversion to and from
vectors can use the same algorithms as in the section above by setting the latitude to zero.

D6.3    Non-GSO satellite coordinate system
D6.3.1 Non-GSO satellite orbit parameters
This section defines the parameters that specify an orbit for non-GSO satellites. Non-GSO satellites
move in a plane as shown in Fig. 49.
```


---

## [Página 117]

```text
                                        Rec. ITU-R S.1503-4                                          115

                                               FIGURE 49
                                            Orbit plane angles




The plane of the orbit is referenced to the Earth by two angles:
               :    longitude of ascending node: This defines where the ascending orbit plane
                     intersects the equatorial plane. As the orbit is fixed in inertial space while the
                     Earth rotates, a time reference for which this angle is valid must be given. In this
                     case it is the start of the simulation
                i:   inclination angle: This is defined as the angle between the plane of the orbit and
                     the equatorial plane.
The orbit and position of the non-GSO satellite within the orbit is then defined by further parameters
as shown in Fig. 50.
```


---

## [Página 118]

```text
116                                      Rec. ITU-R S.1503-4

                                                   FIGURE 50
                                Definition of non-GSO satellite in-plane angles




The shape of the orbit is defined by:
                                              a = (Ra + Rp)/2                                 (12)
                                        e = (Ra – Rp) / (Ra + Rp)                             (13)
where:
                a:   semi-major axis
                e:   eccentricity
               Ra:   distance from the centre of the Earth to the satellite at apogee
               Rp:   distance from the centre of the Earth to the satellite at perigee.
The position of the perigee within the orbit plane is defined by:
               :    argument of perigee, angle between the line of the nodes and perigee.
The position of a non-GSO satellite within the plane at a particular time is defined by:
               v0: angle between perigee and specified point on orbit.
For circular orbits,  can be set to zero and v0 assumed to be the same as the argument of latitude
defined as:
                                               μ0 = ω + υ0                                    (14)
Other useful terms are:
                                              𝑝 = 𝑎(1 − 𝑒 2 )                                 (15)
                                            𝑀 = 𝐸 − 𝑒 sin 𝐸                                   (16)
                                                       1+𝑒       𝐸
                                           tan 2 = √1 – 𝑒 tan 2                               (17)
                                                         𝑝
                                             𝑅 = 1 +𝑒 cos()                                  (18)
```


---

## [Página 119]

```text
                                         Rec. ITU-R S.1503-4                                            117


                                              𝑇 = 2π√𝑎3 /μ                                             (19)
where:
                p:    focal parameter
                E:    eccentric anomaly
                M:    mean anomaly
                T:    period of orbit
                R:    distance from centre of Earth to satellite when satellite is at position .
These can be used by the algorithm to predict the future position of the non-GSO satellite as described
in § D5.
D6.3.2 Non-GSO satellite orbit predictor
Given the orbital elements in the section above, standard orbit mechanics can be used to predict the
position of the satellite at future times. In addition there are three additional precession factors for the
ascending node and argument of perigee as described below.
Line of nodes
                                           3 𝐽 𝑅2        3
                           𝑛 = 𝑛0 (1 + 2 2𝑝2𝑒 (1 – 2 sin2 (𝑖)) (1 – 𝑒 2 )1/2 )                         (20)

where:
         J2 = 0.001082636
                 μ
         𝑛0 = √𝑎3

Orbit precession in ascending node longitude
The rate of ascending node longitude secular drift is defined as:
                                                    3 𝐽 𝑅2
                                         Ω𝑟 = – 2 2𝑝2𝑒 𝑛 cos (𝑖)                                       (21)

It follows from the above that polar orbits have zero precession rate and equatorial ones have
a maximum precession rate. With direct satellite motion (i  90°) the ascending node shifts to the west
(to  decreasing) and with reverse satellite motion (i  90°) it shifts to the east (to  increasing).
Perigee argument precession
Perigee argument secular shift rate is defined as:
                                            3 𝐽 𝑅2           5
                                     ω𝑟 = 2 2𝑝2𝑒 𝑛 (2 – 2 sin2 (𝑖))                                    (22)

Perigee argument precession rate at i = 0 and i = 180 is maximum. For i1 = 63 26' 06'' or
i2 = 116° 33' 54'' the precession rate is zero. If i  i1 or i  i2, then the perigee precession is along
a satellite motion direction, and if i1  i  i2, then it is in the opposite direction.
Use of precession terms
Perigee argument is defined as:
                                               ω = ω0 + ωrt                                            (23)
where:
                0:   perigee argument at an initial moment
```


---

## [Página 120]

```text
118                                      Rec. ITU-R S.1503-4

               r:     perigee argument precession rate.

A current value of an ascending node longitude is defined as:

                                             Ω = Ω0 + Ωrt                                          (24)
where:
              0 :     ascending node longitude at an initial moment
              r:      ascending node longitude precession rate.
The revised orbit period is then:
                                                      2π
                                               𝑇𝑃 = 𝑤 +𝑛̅                                          (25)
                                                      𝑟


The conversion to generic cartesian-based vector would depend upon the direction of the X vector.
For an example coordinate system and for circular orbits, the satellite motion expression in the
geocentric inertial reference system could be defined as:
                       𝑥      𝑅(cos( + ω) cos(Ω) – sin( + ω) sin(Ω) cos(𝑖))
                      [𝑦] = [ 𝑅(cos( + ω) sin(Ω) + sin( + ω) cos(Ω) cos(𝑖)) ]                    (26)
                       𝑧     𝑅 sin( + ω) sin(𝑖)
And the velocity vector could be defined as:


                xhe            h
                   sin(ν) − R (cos(Ω) sin(ω + ν) + sin(Ω) cos(ω + ν)cos (i))
                Rp
          𝑥̇      yhe        h
         [𝑦̇ ] = Rp sin(ν) − R (sin(Ω) sin(ω + ν) − cos(Ω) cos(ω + ν)cos (i))                      (27)
          𝑧̇                    zhe          h
                [                   sin(ν) + R sin(i) cos(ω + ν)             ]
                                 Rp

                                               ℎ = √μ𝑝
                h:     specific angular momentum
ECF (Earth Centered and Fixed) components of the position and velocity vectors could be defined
as:
                                     𝑥         𝑥 cos(θ) + 𝑦 sin(θ)
                                    [𝑦]    = [−𝑥 sin(θ) + 𝑦 cos(θ)]                                (28)
                                     𝑧 𝐸𝐶𝐹              𝑧
                               𝑥̇        𝑥̇ cos(θ) + 𝑦̇ sin(θ) + Ωe 𝑦𝐸𝐶𝐹
                              [𝑦̇ ]   = [−𝑥̇ sin(θ) + 𝑦̇ cos(θ) − Ωe 𝑥𝐸𝐶𝐹 ]                        (29)
                               𝑧̇ 𝐸𝐶𝐹                     𝑧̇
                                                                    𝑟𝑎𝑑
where Ωe is a rotation rate of the earth (7,2921158553 ∗ 10−5 𝑠𝑒𝑐 ) and θ is the Greenwich hour
angle of the Earth’s prime meridian, i.e. the angle between the inertial x axis and ECF x axis.
A satellite motion in an elliptical orbit is non-uniform; therefore, the Kepler expression and a concept
of a mean anomaly would be used in the model to define the real anomaly as a function of time. Since
an explicit dependence of the true anomaly on time is unavailable; the numerical methods of solving
the below expressions were used for its definition. The expression is:
                                            𝑀 = 𝑀0 + 𝑛𝑡                                            (30)
```


---

## [Página 121]

```text
                                        Rec. ITU-R S.1503-4                                          119

Kepler’s equation 𝑀 = 𝐸 − 𝑒𝑠𝑖𝑛(𝐸) can be solved with Newton-Raphson method to obtain the true
anomaly:
                                                           e+cos( )
                                    𝐸0 = 𝑀0 = arccos (1+cos(0 ))                                   (31)
                                                                    0
                                                    E −e sin(E )−M
                                       Ei+1 = Ei − i1−e cos(E
                                                            i
                                                              )
                                                                                                    (32)
                                                               i


D6.3.3 Conversion to Generic Cartesian-based Vectors
The conversion to generic Cartesian-based vector would depend upon the direction of the X vector,
but an approach based upon the X vector aligned with the direction in which the ascending node
longitude is zero is as follows:
1.      For the relevant time t in seconds since simulation start, calculate the values of the processing
        terms (, , ) as required using the decision tree in § D6.3.5.
2.      From M calculate the eccentric anomaly E using equation (16) and iteration.
3.      From E calculate the true anomaly  using equation (17).
4.      Hence calculate the radius vector R using equation (18).
5.      Calculate the position of the satellite within the orbital plane in (P, Q) coordinates defined as
        in Fig. 51 below using:
                                            𝑝       𝑅 cos(𝜈)
                                            𝑞
                                          ( ) = [ 𝑅 sin(𝜈) ]                                         (33)
                                            0            0
6.      Create the rotation matrix from satellite orbit coordinates to inertial xyz coordinates using:
                                             𝑅11     𝑅12    𝑅13
                                        ̃    𝑅
                                        𝑅 = [ 21     𝑅22    𝑅23 ]                                   (34)
                                             𝑅31     𝑅32    𝑅33
        where:
                            𝑅11 = cos(Ω) cos(ω) – sin(Ω) sin(ω) cos(𝑖)                              (35)
                           𝑅12 = − cos(Ω) sin(ω) – sin(Ω) cos(ω) cos(𝑖)                             (36)
                                         𝑅13 = sin(Ω) sin(i)                                        (37)
                           𝑅21 = sin(Ω) cos(ω) + cos(Ω) sin(ω) cos(𝑖)                               (38)
                          𝑅22 = −sin(Ω) sin(ω) + cos(Ω) cos(ω) cos(𝑖)                               (39)
                                        𝑅23 = −cos(Ω) sin(𝑖)                                        (40)
                                         𝑅31 = sin(ω) sin(𝑖)                                        (41)
                                         𝑅32 = cos(ω) sin(𝑖)                                        (42)
                                            𝑅33 = cos(𝑖)                                            (43)
7.      Hence calculate the position of the satellite in xyz coordinates using:
                                             𝑥          𝑝
                                                     ̃
                                            [𝑦] = 𝑅 (𝑞 )                                            (44)
                                             𝑧          0
```


---

## [Página 122]

```text
120                                      Rec. ITU-R S.1503-4

                                                 FIGURE 51
                                    Definition of satellite P, Q coordinates




D6.3.4 Non-GSO satellite orbit station keeping
An important aspect to station keeping is to simulate multiple passes of the non-GSO satellite through
an earth station’s main beam with slightly different crossing directions. As changing the position
within the plane does not affect this, then the main parameter to vary is the longitude of the ascending
node.
The approach proposed is to give range Wdelta in longitude of ascending node. At the start of the
simulation all stations in the constellation have this parameter offset by −Wdelta. During the simulation
this field would increase to 0 (at the run’s mid-point) and then increase to Wdelta.
This is implemented by rotating the station’s position and velocity vectors around the Z axis by the
required angle as specified in § D6.3.4.
D6.3.5 Forced orbit precession
The standard orbit prediction algorithm is based upon point Earth mass plus correcting factors for J2
perturbations. There are two cases where this requires to be over-ridden:
a)      where administrations supply a detailed value of the orbit precession rate with respect to a
        point Earth mass to ensure a repeat ground track;
b)      for non-repeat orbits where an artificial precession rate is used to ensure the required spacing
        between equatorial passes.
D6.3.6 Combining the orbit models
The various options for the orbit model can be combined in three ways as shown Fig. 52.
```


---

## [Página 123]

```text
                                         Rec. ITU-R S.1503-4                                           121

                                                 FIGURE 52
                                       Flowchart of orbit model options




Note that the equatorial orbit i = 0 constellation is a special case in that there is no station keeping but
the ground track of each satellite repeats just after one orbit. It should therefore be treated as Case (1)
but with forced precession set to zero as described in § D4.
The three cases have their key orbit angles in radians updated as follows:
Case 1
                                              ω(𝑡) = 𝑤0 + ω𝑟 𝑡                                         (45)
                                                           π
                                  Ω(𝑡) = Ω0 + Ω𝑟 𝑡 + 180 𝐷𝑎𝑟𝑡𝑖𝑓𝑖𝑐𝑖𝑎𝑙 𝑡                                 (46)
                                              𝑀(𝑡) = 𝑀0 + 𝑛̅𝑡                                          (47)
Case 2
                                              ω(𝑡) = 𝑤0 + ω𝑟 𝑡                                         (48)
                                                       π              2𝑡
                              Ω(𝑡) = Ω0 + Ω𝑟 𝑡 + 180 . 𝑊𝑑𝑒𝑙𝑡𝑎 (𝑇            − 1)                       (49)
                                                                      𝑟𝑢𝑛

                                              𝑀(𝑡) = 𝑀0 + 𝑛̅𝑡                                          (50)
Case 3
                                                 ω(𝑡) = 𝑤0                                             (51)
                                          π                 π               2𝑡
                          Ω(𝑡) = Ω0 + 180 𝐷𝑎𝑑𝑚𝑖𝑛 𝑡 + 180 . 𝑊𝑑𝑒𝑙𝑡𝑎 (𝑇              − 1)                 (52)
                                                                            𝑟𝑢𝑛

                                              𝑀(𝑡) = 𝑀0 + 𝑛0 𝑡                                         (53)
where:
             r =     J2 precession of longitude of the ascending node in radians / second
```


---

## [Página 124]

```text
122                                      Rec. ITU-R S.1503-4

              r =    J2 precession of the argument of perigee in radians / second
               𝑛=     orbit motion including the J2 term in radians / second
              n0 =    orbit motion for point mass in radians / second
      𝐷𝑎𝑟𝑡𝑖𝑓𝑖𝑐𝑖𝑎𝑙 =   artificial precession in degrees / second
        𝐷𝑎𝑑𝑚𝑖𝑛 =      admin supplied precession in degrees / second
         Wdelta =     station keeping range in degrees
              t=      simulation time in seconds
           Trun =     total run time of the simulation in seconds.
D6.3.7 Mapping orbit parameters from SRS data
The following orbit parameters are given in the SRS / IFIC database:
Table orbit:
–       Apogee height (km) = ha
–       Perigee height (km) = hp
–       Inclination angle (degrees) = INC
–       Right ascension (degrees) = RA
–       Longitude of ascending node (degrees) = LAN
–       Argument of perigee (degrees) = AP.
Table phase:
–       Phase angle (degrees) = PA.
For most of these fields it is possible to map nearly directly to the orbit parameters required, such as:
                                                        ℎ𝑎 + ℎ𝑝
                                           𝑎 = 𝑅𝑒 +        2
                                                    ℎ𝑎 – ℎ𝑝
                                               𝑒=     2𝑎

                                                 i = INC
                                                 = LAN
                                                  = AP
Note that this algorithm uses the ascending node definition based upon longitude rather than right
ascension to ensure the orbit is correctly referenced to earth station longitude.
The last field to define is the true anomaly, , which can be derived from the phase angle. The phase
angle is defined in RR Appendix 4 as:
 A.4.b.5.b:      the initial phase angle (i) of the i-th satellite in its orbital plane at reference time
                 t = 0, measured from the point of the ascending node (0° ≤ i < 360°)

The phase angle is shown in Fig. 53.
```


---

## [Página 125]

```text
                                         Rec. ITU-R S.1503-4                                           123

                                                FIGURE 53
                                          Definition of phase angle




The true anomaly can therefore be derived from the phase angle as follows:
                                              0 = 𝑃𝐴 – ω
or:
                                          𝑃𝐴 = ω + 0 = μ0

D6.4    Geometry
D6.4.1 Distance between two stations
Given two station’s position vectors in the form (x, y, z), the distance D between them can be
calculated using:
                             𝐷 = √(𝑥1 − 𝑥2 )2 + (𝑦1 − 𝑦2 )2 + (𝑧1 − 𝑧2 )2

D6.4.2 Distance to Earth horizon
The distance to the horizon Dh for a station with Earth centred position vector of magnitude R can be
calculated as follows:
                                            𝐷ℎ = √𝑅 2 – 𝑅𝑒2

D6.4.3 Satellite visibility check
Two stations, whether earth stations or satellites, are visible if the direct distance between them is less
than the sum of the distance to the horizon for each station, using the spherical Earth model described
in § D6.1.

D6.4.4 Angle to GSO arc and Longitude
D6.4.4.1 Definition
Figure 54 shows the definition of the  angle.
```


---

## [Página 126]

```text
124                                     Rec. ITU-R S.1503-4

                                                FIGURE 54
                                           Definition of  angle




The Figure shows a test earth station and non-GSO satellite.
For each test point Pi on the GSO arc, there is a line from the earth station that intersects that point.
There is then an angle i between that line and a line from the earth station to the non-GSO satellite.
The  angle is the minimum of all the test points for which the line does not intersect the Earth, i.e.
                                              α = min(α1 )
The sign of  is determined by whether the line from the earth station to the non-GSO satellite
intersects the XY plane at a distance less than or greater than the GSO radius as follows:
Given:
         Earth station position vector:              RES
         Non-GSO satellite position vector:          RNS
         Construct line:

                                           𝑅 = 𝑅𝐸𝑆 + λ𝑅𝐸𝑁
where:

                                          R EN = R NS − R ES
         This line crosses the XY plane when:
                                                R(z) = 0
         i.e. when
                                                     – 𝑅𝐸𝑆 (𝑧)
                                           λ𝑧=0 =
                                                      𝑅𝐸𝑁 (𝑧)
Hence:

                                      R z =0 = R ES +  z =0 R EN
```


---

## [Página 127]

```text
                                            Rec. ITU-R S.1503-4                                      125

The sign of , X is then determined by:
         If λ𝑧=0 < 0 then Rz=0 = Infinity
         In the case when the ES is in the northern hemisphere:
             If Rz=0 < Rgeo then  is positive
             If Rz=0 = Rgeo then  is zero
             If Rz=0 > Rgeo or if z=0 ≤ 0 then  is negative.
         In the case when the ES is in the southern hemisphere:
             If Rz=0 > Rgeo then  is positive
             If Rz=0 = Rgeo then  is zero
             If Rz=0 < Rgeo or if z=0  0 then  is negative.
In the case when the ES is at the equator:
              is the negative of the sign of 𝑅𝐸𝑁 (𝑧)
From the test point identified to give the  angle, the longitude can be calculated between the non-
GSO sub-satellite point and the point on the GSO arc where the  angle is minimised, as shown in
Fig. 55.

                                                   FIGURE 55
                                             Definition of longitude




Hence:
         Long = LongAlpha – LongNGSO
The  iteration should use test points that meet the requirements in § D1.4.
If there are two points on the GSO arc that give the same  (possibly the two edge of visibility points),
then the one with the minimum absolute Long should be selected. If both have the same Long but
with different signs, then the positive one should be used.
```


---

## [Página 128]

```text
126                                      Rec. ITU-R S.1503-4

D6.4.4.2 Alpha search range
Note that the GSO arc will be visible to the non-GSO at a height of hnGSO and latitude latnGSO if the
difference in longitude is less than:
                                                              cos 𝑥
                                         cos ∆𝑙𝑜𝑛𝑔 = cos 𝑙𝑎𝑡
                                                                   𝑛𝐺𝑆𝑂

where:
                                                𝑥 = 𝑥1 + 𝑥2
and:
                                                             𝑅
                                               cos 𝑥1 = 𝑅 𝑒
                                                             𝑔𝑠𝑜

                                                             𝑅𝑒
                                            cos 𝑥2 = 𝑅 +ℎ
                                                         𝑒       𝑛𝐺𝑆𝑂

The range of the GSO arc that will be visible to an ES can be calculated in a similar manner by setting
x2 = 0.
D6.4.4.3 Sign of Alpha
Figures 56 and 57 below are provided to give clarity as to the sign of  as seen from the perspective
of a non-GSO satellite or non-GSO ES in the northern and southern hemispheres.
When the non-GSO satellite is in the northern hemisphere:

                                                  FIGURE 56
                         Sign of  as seen by non-GSO satellite in Northern Hemisphere
```


---

## [Página 129]

```text
                                       Rec. ITU-R S.1503-4                                127

                                                FIGURE 57
                   Sign of  as seen by non-GSO ES in Northern Hemisphere looking South




The geometry for the case when the non-GSO satellite is in the southern hemisphere is shown in
Figs 58 and 59 below.

                                                FIGURE 58
                       Sign of  as seen by non-GSO satellite in Southern Hemisphere
```


---

## [Página 130]

```text
128                                      Rec. ITU-R S.1503-4

                                                 FIGURE 59
                     Sign of  as seen by non-GSO ES in Southern Hemisphere looking North




D6.4.4.4 Analytic method to calculate  and 
This section describes an analytic method to calculate the alpha angle and also associated  angles.
Analytic calculation of 
The analytic method to calculate  starts with the following two inputs:
       P = Position vector of GSO ES, typically with magnitude = radius of the Earth = Re
       N = Position vector of non-GSO satellite
These are defined as:
                                                     𝑥𝑝
                                                𝑷 = (𝑦𝑝 )
                                                     𝑧𝑝
                                                     𝑥𝑛
                                                𝑵 = ( 𝑦𝑛 )
                                                      𝑧𝑛
Then assume:
       G = Position vector of a point on GSO arc specified by angle  and radius of geostationary
       orbit Rgeo:
                                                 𝑅𝑔𝑒𝑜 cos θ
                                            𝑮 = ( 𝑅𝑔𝑒𝑜 sin θ )
                                                      0
Then  is the minimum over all  of the angle between the lines PN and PG where:
                                                 𝑥𝑛 − 𝑥𝑝
                                        𝑷𝑵 = ( 𝑦𝑛 − 𝑦𝑝 )
                                                 𝑧𝑛 − 𝑧𝑝
```


---

## [Página 131]

```text
                                       Rec. ITU-R S.1503-4                              129

                                            𝑅𝑔𝑒𝑜 cos θ − 𝑥𝑝
                                      𝑷𝑮 = ( 𝑅𝑔𝑒𝑜 sin θ − 𝑦𝑝 )
                                                  −𝑧𝑝
This can be calculated using:
                                                           𝑷𝑵.𝑷𝑮
                                           cos α = |𝑷𝑵||𝑷𝑮|

This will be minimised over  when:
                                                    𝑑
                                                         =0
                                                    𝑑θ

Which is also when:
                                          𝑑   𝑷𝑵.𝑷𝑮
                                            [        ]=0
                                          𝑑θ |𝑷𝑵||𝑷𝑮|

Writing this as:
                                                𝑑    𝑓
                                                    [ ]=0
                                                𝑑θ 𝑔

Then f can be written as:
                                      𝑓 = 𝐴 + 𝐵 cos θ + 𝐶 sin θ
where:
                        𝐴 = −[(𝑥𝑛 − 𝑥𝑝 )𝑥𝑝 + (𝑦𝑛 − 𝑦𝑝 )𝑦𝑝 + (𝑧𝑛 − 𝑧𝑝 )𝑧𝑝 ]
                                         𝐵 = (𝑥𝑛 − 𝑥𝑝 )𝑅𝑔𝑒𝑜
                                         𝐶 = (𝑦𝑛 − 𝑦𝑝 )𝑅𝑔𝑒𝑜
And g can be written:
                                   𝑔 = √𝐸 + 𝐹 cos θ + 𝐺 sin θ
where:
                                                2
                                           𝐸 = 𝑅𝑔𝑒𝑜 + 𝑅𝑒2
                                           𝐹 = −2𝑥𝑝 𝑅𝑔𝑒𝑜
                                           𝐺 = −2𝑦𝑝 𝑅𝑔𝑒𝑜
Note that D = magnitude of PN is not dependent upon  and so is a constant which need not be
considered further.
using:
                                                𝑔 = √𝑔𝑟
and noting that:
                                                𝑑    𝑓
                                                    [ ]=0
                                                𝑑θ 𝑔

when:
                                            𝑑        𝑓 2
                                                [( ) ] = 0
                                           𝑑θ        𝑔

i.e. when:
```


---

## [Página 132]

```text
130                                     Rec. ITU-R S.1503-4

                                                𝑑    𝑓2
                                                 [ ]=0
                                               𝑑θ 𝑔       𝑟

using f’ to represent f differentiated by , this can be expanded using standard methods as:
                                            2𝑓𝑓′𝑔𝑟 −𝑔𝑟 ′𝑓 2
                                                              =0
                                                    𝑔𝑟2

This can be simplified to:
                                             2𝑓′𝑔𝑟 = 𝑔𝑟 ′𝑓
using:
                                                𝑥 = sin θ
Note there is an alternative solution that uses cos  instead of sin .
Simplified using a new set of parameters {a, b, c, d, e} this is:
                                [𝑎 + 𝑏𝑥]2 (1 − 𝑥 2 ) = [𝑐 + 𝑑𝑥 + 𝑒𝑥 2 ]2
where:
                                            𝑎 = 𝐴𝐺 − 2𝐶𝐸
                                             𝑏 = 𝐵𝐹 − 𝐶𝐺
                                            𝑐 = 2𝐶𝐹 − 𝐵𝐺
                                            𝑑 = 𝐴𝐹 − 2𝐵𝐸
                                            𝑒 = −𝐵𝐺 − 𝐶𝐹
This can be expanded and then re-arranged into:
                                𝑎4 𝑥 4 + 𝑎3 𝑥 3 + 𝑎2 𝑥 2 + 𝑎1 𝑥 + 𝑎0 = 0
where:
                                             𝑎4 = 𝑒 2 + 𝑏 2
                                           𝑎3 = 2𝑑𝑒 + 2𝑎𝑏
                                       𝑎2 = 𝑑 2 + 2𝑐𝑒 + 𝑎2 − 𝑏 2
                                            𝑎1 = 2𝑐𝑑 − 2𝑎𝑏
                                             𝑎0 = 𝑐 2 − 𝑎 2
This quartic equation can be solved using a number of techniques, including the use of the Newton-
Raphson method1. This approach aims to solve a function f of a variable x, using iteration, given an
initial value and f’ = the derivative of f using:
                                                              𝑓(𝑥 )
                                          𝑥𝑛+1 = 𝑥𝑛 − 𝑓′ (𝑥𝑛 )
                                                                 𝑛

where:
                                                𝑥 = sin θ
                              𝑓(𝑥) = 𝑎4 𝑥 4 + 𝑎3 𝑥 3 + 𝑎2 𝑥 2 + 𝑎1 𝑥 + 𝑎0
                                𝑓′(𝑥) = 4𝑎4 𝑥 3 + 3𝑎3 𝑥 2 + 2𝑎2 𝑥 + 𝑎1



1   https://en.wikipedia.org/wiki/Newton%27s_method
```


---

## [Página 133]

```text
                                         Rec. ITU-R S.1503-4                                           131

The Newton-Raphson method is considered to have converged when the absolute difference between
xn and xn+1 is less than 1e-6.
For the starting value, both x = +1 and x = −1 can be used and two solutions derived, matching the
two cases of the line from the GSO ES position P to the non-GSO satellite N forward or backwards.
This analytic method does not take account of the range of visibility of the GSO arc as seen by the
GSO ES. It is therefore necessary to identify the maximum range of the visible GSO arc, using:
                                                          𝑅𝑒
                                        cos θmax = 𝑅
                                                      𝑔𝑒𝑜 cos 𝐿𝑎𝑡𝐸𝑆

The points on the GSO arc that correspond to max can be considered additional possible solutions.
The solution to use is one that is within the range −max to max that results in the smallest alpha angle.
The iterative method may be used as a fall-back should these methods fail to derive suitable solutions
to the quartic equation but the preferred approach is analytic.
Analytic calculation of 
The beta angle is defined in a similar way to that for  but is minimised over all possible positions of
the non-GSO satellite with radius vector Rn and at latitude = lat but unknown longitude, for a given
position of the GSO satellite.
The analytic method to calculate  starts with the following two inputs:
       P = Position vector of GSO ES, typically with magnitude Rp = radius of the Earth = Re
       G = Position vector of GSO satellite with magnitude Rg
These are defined as:
                                                    𝑥𝑝
                                               𝑃 = (𝑦𝑝 )
                                                    𝑧𝑝
                                                    𝑥𝑔
                                               𝐺 = (𝑦𝑔 )
                                                    0
Then assume:
               N=     Position vector of non-GSO satellite at latitude = lat and angle  and radius of
                      geostationary orbit Rn:
                                             𝑅𝑛 cos 𝑙𝑎𝑡 cos θ
                                        𝑁 = ( 𝑛 cos 𝑙𝑎𝑡 sin θ)
                                             𝑅
                                                𝑅𝑛 sin 𝑙𝑎𝑡
Then  is the minimum over all  of the angle between the lines PN and PG where:
                                          𝑅𝑛 cos 𝑙𝑎𝑡 cos θ − 𝑥𝑝
                                    𝑃𝑁 = (𝑅𝑛 cos 𝑙𝑎𝑡 sin θ − 𝑦𝑝 )
                                             𝑅𝑛 sin 𝑙𝑎𝑡 − 𝑧𝑝
                                                𝑥𝑔 − 𝑥𝑝
                                         𝑃𝐺 = ( 𝑦𝑔 − 𝑦𝑝 )
                                                  −𝑧𝑝
This can be calculated using as similar approach above, with f and gr functions:
                                       𝑓 = 𝐴 + 𝐵 cos θ + 𝐶 sin θ
                                      𝑔𝑟 = 𝐸 + 𝐹 cos θ + 𝐺 sin θ
```


---

## [Página 134]

```text
132                                       Rec. ITU-R S.1503-4

where:
                              𝐴 = 𝑅𝑃2 − (𝑥𝐺 𝑥𝑃 + 𝑦𝐺 𝑦𝑃 + 𝑧𝑝 𝑅𝑁 sin 𝑙𝑎𝑡)
                                        𝐵 = (𝑥𝐺 − 𝑥𝑃 )𝑅𝑁 cos 𝑙𝑎𝑡
                                        𝐶 = (𝑦𝐺 − 𝑦𝑃 )𝑅𝑁 cos 𝑙𝑎𝑡
and:
                                     𝐸 = 𝑅𝑁2 + 𝑅𝑃2 − 2𝑧𝑃 𝑅𝑁 sin 𝑙𝑎𝑡
                                           𝐹 = −2𝑥𝑃 𝑅𝑁 cos 𝑙𝑎𝑡
                                           𝐺 = −2𝑦𝑃 𝑅𝑁 cos 𝑙𝑎𝑡
Note that D = magnitude of PG is not dependent upon  and so is a constant which need not be
considered further.
When the parameter set {A, B, C, E, F, G} has been defined then a similar methodology can be used
to solve for  as for  above. There can be cases, such as its use in the worst case geometry algorithm,
where it is not required to check for visibility.
D6.4.4.5 DeltaLongES
The difference in longitude for a non-GSO ES and a point on the GSO arc, as used in the definition
of the ES_e.i.r.p. mask, is defined as follows:
                 DeltaLongES = Longitude(GSO point) – Longitude(Non-GSO ES)
D6.4.5 Satellite and earth station azimuth and elevation
Figure 60 shows the definition of the azimuth and elevation angles used for the non-GSO satellite:

                                                  FIGURE 60
                            Definition of azimuth and elevation for non-GSO satellite




It should be noted that direction of the cartesian X, Y, Z vectors in this diagram are:
         X: +ve in the East direction from the non-GSO satellite
```


---

## [Página 135]

```text
                                          Rec. ITU-R S.1503-4                                         133

        Y: towards the centre of the Earth from the non-GSO satellite
        Z: +ve towards the North direction from the non-GSO satellite.
For the earth station, the definition of the azimuth and elevation angles are as in Fig. 61.

                                                  FIGURE 61
                              Definition of azimuth and elevation for earth station




It should be noted that direction of the cartesian X, Y, Z vectors in this diagram are:
         X: +ve in the East direction from the earth station in the horizontal plane
         Y: towards the North direction from the earth station in the horizontal plane
         Z: +ve towards the Zenith of the earth station perpendicular to the horizontal plane.
Furthermore, the normalized coordinates, u and v, can be computed from the azimuth and elevation
angles as follows:
                                         𝑢 = cos(𝐸𝑙) ∗ cos(𝐴𝑧)
                                         𝑣 = cos(𝐸𝑙) ∗ sin(𝐴𝑧)

D6.5    Gain patterns
This section defines the gain patterns used in the algorithms for earth stations and satellites. Note that
all formula includes the peak gain, so where relative gain is required, the peak gain should be
subtracted.
D6.5.1 GSO earth station gain patterns
D6.5.1.1 FSS earth station gain pattern
The FSS earth station gain pattern to use is specified in Recommendation ITU-R S.1428.
D6.5.1.2 BSS earth station gain pattern
The BSS earth station gain pattern to use is specified in Recommendation ITU-R BO.1443.
```


---

## [Página 136]

```text
134                                      Rec. ITU-R S.1503-4

D6.5.2 GSO satellite gain pattern
The values of peak gain and half power beamwidth and the antenna reference radiation pattern to use
are specified in RR Article 22 based upon Recommendation ITU-R S.672.
The peak gain to use in analysis should be selected using Table 16.

                                              TABLE 16
                      Peak gain to use with Recommendation ITU-R S.672
            Half power beamwidth in Article 22             Peak gain to use in analysis
                            1.5                                      41.0 dBi
                           1.55                                      40.7 dBi
                             4                                       32.4 dBi


D7       Structure and format of results

D7.1     Go/No-go decision
D7.1.1 Overall description of the decision process
The simulation produces a probability distribution function (PDF) of the pfd. The PDF records, for
each pfd level, the number of simulation time-steps at which that pfd level occurred divided by the
sum of all bins. The PDF should be converted into a cumulative distribution function (CDF) which
records for each pfd level the number of simulation time-steps at which that pfd level was exceeded
normalized by the total number of simulation time steps.
Note that the term cumulative distribution function is meant to include the concept of the
complementary cumulative distribution function depending upon context.
D7.1.2 Production of the CDF
The process detailed in § D5 generated a PDF of the pfd values. This PDF should be converted into
a CDF which records for each pfd level an estimate of the percentage of time during which that pfd
level is exceeded.
For each pfd value, the CDF should be calculated by:
                                  CDFi = 100 (1 – SUM (PDFmin: PDFi))
where:
            PDFx:    PDF table entry for a pfd value of X dB, normalized so that the total sum for all
                     PDFx is 1.
D7.1.3 Comparison procedure
The next stage is the comparison of the pfd limit values in the RR with those in the probability table.
Step 1: Perform Steps 2 through 4 for each specification limit i.
Step 2: Read the pfd value/probability pair (Ji and Pi) from the database.
Step 3: If the pfd value Ji has a higher precision than SB (currently 0.1 dB) round Ji to a lower value
        with a maximum precision of 0.1 dB.
Step 4: From the CDF find Pt, the probability that pfd value Ji was exceeded as obtained by the
        software.
```


---

## [Página 137]

```text
                                            Rec. ITU-R S.1503-4                                      135

Step 5: If Pi  Pt then record Pass: the CDF complies with this specification point. Else record Fail:
        the CDF does not comply with this specification point.
The final stage is the comparison of the maximum pfd value recorded during the software run with
the limit specified for 100% time (if any).
From the CDF, find the maximum pfd value recorded during the software run, Jmax. Compare it with
the pfd limit specified for 100% time, J100. If Jmax  J100 then record Pass: the CDF complies with this
specification point. If Jmax  J100 then record Fail: the CDF does not comply with this specification
point.
D7.1.4 Decision process
If a Pass result was recorded for all of the specification limits, then the non-GSO network complies
with the specification. If any Fail results were recorded, then the non-GSO network does not comply
with the specification.
D7.2       Background information to decision
The background information required is:
–      pfd data generated in the software run (including antenna diameter) and reference antenna
       pattern;
–      table of specification limits for various antenna diameters and reference antenna pattern.
D7.3       Format for output
The output format should be:
–          statement of the result of the test;
–          summary table;
–          CDF table (for information only).
D7.3.1 Statement of the result of the compliance test
The overall conclusion of the evaluation (Pass or Fail) as defined in § D7.1.4 should be output.
D7.3.2 Summary table
The summary table should show the following data (see Table 17):

                                                     TABLE 17
                                                  Summary table
                   Specification point                             Result         Simulation point
 pfd value                             Probability                                    Probability
              2
 J1 dB(W/(m · BWref))                      P1                      Pass/fail              Py
 :                                          :                          :                   :
              2
 Ji dB(W/(m · BWref))                      Pi                      Pass/fail              Py


where:
     Ji and Pi:    pfd value/probability specification values from the database
     Pass/fail:    test result
     Py:           probability value from the probability table.
```


---

## [Página 138]

```text
136                                     Rec. ITU-R S.1503-4



D7.3.3 Probability table
The output should include for information the calculated CDF which was used in the decision-making
process.




                                              PART E

                         Testing of the reliability of the software outputs


E1      Evaluation of the computation accuracy of the candidate software
These tests could be performed by the software developer, and the results supplied to the BR along
with the candidate software.
Software functions to be evaluated:
Orbit projection – Using a set of simplified parameters which result in a defined repeat period, run the
software for the required simulation interval and check the actual (satellite vectors) against the
predicted values.
Offset angles – Using appropriate sets of earth station and satellite locations, check the actual beam
offset angle values against the predicted values. The sets of test data should cover the most complex
trigonometrical cases: for example sites around longitude zero and longitude 180°.
Time step and simulation duration – Using appropriate sets of non-GSO network parameters, check
the time step and simulation duration values generated by the software against the predicted values.
CDF generation – Using sets of test input files with known CDF results, verify the CDF generation
software.
Go/no-go decision process – Using sets of CDF test input files, verify the accuracy of the go/no-go
decision process.
Should multiple implementations be available then sensitivity analysis could be used to evaluate
them, and their output can be compared to ensure consistency.


E2      Evaluation of the epfd(↓/↑) statistics obtained by the BR
These are tests which will be performed automatically by the software as part of each run, to confirm
that the run did find the worst-case interference events.
epfd value for 100% time – the epfd↓ value for 100% time obtained during the run should be
compared with a value calculated from analysis of the non-GSO constellation. The obtained value
should be within ±0.X dB of the expected value.


E3      Verification of the pfd masks
The pfd masks are inputs to the BR validation tool to be provided by the notifying administration to
the BR together with the software used for its calculation, the complete software description and
```


---

## [Página 139]

```text
                                        Rec. ITU-R S.1503-4                                          137

parameters. The information required to generate the pfd mask could be made available to interested
administrations to be used in case of dispute.


E4      Re-testing of the BR software after any modifications or upgrades
A set of tests should be defined for use on any occasions when the BR software or its operating
environment has been modified or upgraded. Such tests could include:
a)      some or all of the tests defined in § E1 for the initial evaluation of the computational accuracy
        of the candidate software;
b)      repetition of a representative set of evaluations of actual non-GSO filings, and comparison
        of the results obtained by the original and modified software systems.




                                               PART F

                          Software implementing this Recommendation


F1      Operating system
The software should run on Microsoft platforms under the Windows 10 or higher operating system.


F2      Interfaces to existing software and databases
The BR captures all incoming notices related to space services into one central database for
alphanumeric data (SNS) and into another database for graphical data (GIMS) such as antenna
diagrams and service areas. These databases are used for the publication of the data on DVD, in the
Weekly Circular and its Special Sections. They are also used to provide input data to software
packages performing RR Appendix 8 and pfd examinations. Graphical Interface for Batch
Calculations (GIBC) is used to carry out examination using these different modules. This guarantees
that the data published are also the data used in these examinations. The BR considers this important
for both the notifying administration and for administrations the services of which may be affected
by the new station. This is not only for the convenience of the BR, but to ensure consistency and
transparency towards administrations.


F3      User manual
The purpose of the user manual is to tell the user how to run different tests to obtain certain results.
Given the complexity of these tests, they need to be given in detail.
```
