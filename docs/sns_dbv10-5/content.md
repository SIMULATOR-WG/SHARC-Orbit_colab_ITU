# SNS Database Structure v10.5

> Extraído de `sns_dbv10-5.pdf` via pdftotext -layout. Páginas renderizadas em `images/page-<n>.png` (diagramas ER são vetoriais — consultar PNG da página).



---

## [Página 1]

```text
    Details relating to the contents of the SNS data items published in Part I-S, II-S, III-S and the Special Sections of the BR IFIC

Table Name      Data Item      Items in AP4     Format      4/2   4/3    Plans                                     Description                                          Comment
adm_assoc                                                                        Administration list “on behalf of” which submitted
             ntc_id           BR              9(9)          x             x      unique identifier of the notice                                               PK, FK; see NOTE 1
             adm              A.1.f.2         X(3)          x             x      country symbol of the notifying administration                                PK; see NOTE 1
aesim_para                                                                       aeronautical earth stations in motion (A-ESIM) table
m
             grp_id           BR             9(9)           x                 identification of the group                                                   PK; see NOTE 1
             aesim_elev_min   A.36.a/C.10.d. S9(3).9(2)     x           AP30B minimum elevation angle at which any associated non-GSO A-
                              10                                        ESIM ESIM/Appendix 30B A-ESIM can transmit to a non-GSO satellite in the
                                                                              frequency band 27.5-29.1 GHz and 29.5-30 GHz/to GSO satellite
             fuse_mask_id     A.36.b/C.10.d. 9(4)           x           AP30B identifier of the aircraft fuselage attenuation mask associated with the non-
                              11                                        ESIM GSO A-ESIM/Appendix 30B A-ESIM and based on ITU-R
                                                                              Recommendations
assgn                                                                         Assigned frequency
             grp_id                           9(9)          x     x       x   unique identifier of the group                                                PK, FK; see NOTE 1
             seq_no                           9(4)          x     x       x   sequence number                                                               PK; see NOTE 1
             freq_sym         C.2.a.1.a       X             x     x       x   symbol indicating kilohertz [K], megahertz [M] or gigahertz [G]
             freq_assgn       C.2.a.1.b       k:9(5).9(3)   x     x       x   assigned frequency
                                              m:9(5).9(6)
                                              g:9(4).9(9)
             freq_mhz         BR              9(7).9(6)                          frequency in MHz                                                              derived data
             f_cmp_rec        BR              X                                  code indicating if two records compared are equal [E], have basic             BR internal data
                                                                                 differences [B], have non-basic differences [N] or the second record is not
                                                                                 found [X]
c_pfd                         A.17                                               Compliance with pfd limits
             ntc_id                           9(9)          x                    unique identifier of the notice                                               PK, FK; see NOTE 1
             seq_no                           9(4)          x                    sequence number                                                               PK; see NOTE 1
             freq_min                         9(7).9(6)     x                    lower frequency limit of the band [MHz]
             freq_max                         9(7).9(6)     x                    upper frequency limit of the band [MHz]
             pfd                              S9(3).9(2)    x                    pfd value in dB(W/m²)
             bdwdth                           9(8).9(2)     x                    bandwidth (in kHz) over which pfd was calculated
             ra_stn_type                      X             x                    type of radio astronomy station: S - single-dish, V - VLBI
carrier_fr                                                                       carrier frequency of the emissions
             grp_id                           9(9)          x                    unique identifier of the group                                                PK, FK; see NOTE 1
             seq_emiss                        9(4)          x                    sequence number of the emission                                               PK, FK; see NOTE 1
             seq_no                           9(4)          x                    sequence number                                                               PK; see NOTE 1
             freq_carr        C.7.b           9(6).9(6)     x                    carrier frequency in MHz
cmr_grp_ln                                                                       To link 'cmr_syst' to 'grp'
k
             ntc_id                           9(9)                               unique identifier of the notice                                               PK, FK; see NOTE 1
```


---

## [Página 2]

```text
Table Name      Data Item     Items in AP4     Format     4/2   4/3   Plans                                   Description                                            Comment
             seq_cmr                         9(4)                             sequence number of the commercial system pertaining to the network             PK, FK; see NOTE 1
                                                                              submitted on the notice
             grp_id                          9(9)                             unique identifier of the group (Res49)                                         PK, FK; see NOTE 1
cmr_notice                                                x                   Table linking Res552 submission and ITU spacecraft Id.
             ntc_id                          9(9)         x                   unique identifier of the notice                                                PK, FK; see NOTE 1
             itu_scraft_id                   9(9)         x                   unique identifier of the spacecraft                                            PK, FK
             reg_st                          X            x                   code indicating regulatory status (F = First bringing into use, S =
                                                                              Suspended, R= Resumed)
             d_reg_st                        9(8)         x                   Date of first bringing into use / suspending / resuming
             rsn_susp                        X(255)       x                   reason for suspension
cmr_syst                                                                      Table to identify commercial satellite system submitted under RES49
             ntc_id          BR              9(9)         x            x      unique identifier of the notice                                                PK, FK; see NOTE 1
             seq_no          BR              9(4)         x            x      sequence number of the commercial system pertaining to the network             PK; see NOTE 1
                                                                              submitted on the notice
             ntwk_name                       X(30)        x            x      commercial name of the satellite
             lsp_name                        X(20)        x            x      name of the launch service provider
             vehicle                         X(20)        x            x      name of the launch vehicle
             d_exe                           9(8)         x            x      date of execution of the launch contract
             d_deliv_fr                      9(8)         x            x      starting limit of the anticipated launch or in-orbit "delivery window"
             d_deliv_to                      9(8)         x            x      end limit of the anticipated launch or in-orbit "delivery window"
             facility                        X(20)        x            x      name of the launch facility
             mfct_name                       X(20)        x            x      name of the manufacturer
             nbr_sat                         9(9)         x            x      number of satellites procured
             d_exe_m                         9(8)         x            x      date of execution of the contract
             d_deliv_fr_m                    9(8)         x            x      starting limit of the contractual "delivery window"
             d_deliv_to_m                    9(8)         x            x      end limit of the contractual "delivery window"
coord_agre                   A5/A6                        x                   Network-level coordination agreements
e_ntw
             ntc_id                          9(9)         x                   unique identifier of the notice                                                PK, FK; see NOTE 1
             coord_prov      A.5.c/A.6.c     X(20)        x                   provision code for form of coordination                                        PK
             adm             A.5.a.1/A.6.a   X(3)         x                   country symbol of the notifying administration
             ntwk_org        A.5.a.2/A.6.b   X(3)         x                   symbol of the organization operating regional or international networks
                                                                              (Table 2 of the Preface to BR IFIC (Space Services))
             sat_name        A.5.a.2.a/A.6.a X(30)        x                   name of the satellite network or system for which agreement has been           PK
                             .1                                               successfully effected/reached for all notified assignments
             long_nom                        S9(3).9(2)   x                   nominal longitude of the space station identified in A.5.a.2.a/A.6.a.1, give
                                                                              '-' for West '+' for East
e_ant                                                                         Earth station antenna
             ntc_id                          9(9)               x             unique identifier of the notice                                                PK, FK; see NOTE 1
             emi_rcp         B.2             X                  x             code identifying a beam as either transmitting [E] or receiving [R]            PK
             beam_name       B.1.a           X(8)               x             designation of the satellite antenna beam                                      PK
             act_code                        X                  x             code indicating the action to be taken on the entity                           see NOTE 3
```


---

## [Página 3]

```text
Table Name      Data Item    Items in AP4         Format   4/2   4/3   Plans                                    Description                                             Comment
             beam_old                         X(8)               x             previous designation of the satellite antenna beam                              in case the beam designation
                                                                                                                                                               is to be changed
             bmwdth         B.5.b             9(3).9(2)          x             beamwidth of the earth station antenna
             attch_e        B.5.c.1           9(2)               x             number of the attachment for the co-polar radiation pattern diagram           see NOTE 2
             attch_e_x      B.5.c.1.b         9(2)                             number of the attachment for the cross-polar radiation pattern diagram        see NOTE 2
             gain           B.5.a             S9(2).9(1)         x             maximum isotropic gain of the earth station antenna
             pattern_id     B.5.c.2.a/B.6.a   9(9)               x             unique identifier of the co-polar radiation pattern in the reference table    see NOTE 4
                                                                               ant_type
             pattern_id_x   B.5.C.2.B         9(9)                             unique identifier of the cross-polar radiation pattern in the reference table see NOTE 4
                                                                               ant_type
             ant_diam       A.7.f/B.6.b       9(3).9(2)          x             antenna diameter (meters)
             dgso           B.5.d             9(3).9(2)          x     30B     Antenna dimension aligned with the geostationary arc (DGSO) (m)
             attch_crdn     A.10.a            9(2)               x             number of the attachment for the earth station coordination diagram           see NOTE 2
             f_fdg_reqd                       X                                code indicating if finding is required                                        BR internal data
             cmp_ntc_id                       9(9)                             ntc_id of the second beam if two beams are compared                           BR internal data
             cmp_beam                         X(8)                             beam_name of the second beam if two beams are compared                        BR internal data
             f_cmp_str                        X                                code indicating if two structures compared are equal [E], have basic          BR internal data
                                                                               differences [B], have non-basic differences [N] or the second structure is
                                                                               not found [X]
             f_cmp_rec                        X                                code indicating if two records compared are equal [E], have basic             BR internal data
                                                                               differences [B], have non-basic differences [N] or the second record is not
                                                                               found [X]
e_ant_elev                                                                     Earth antenna elevation
             ntc_id                           9(9)               x      x      unique identifier of the notice                                               PK, FK; see NOTE 1
             azm            A.7.e.1           9(3).9             x      x      azimuth in degrees measured clockwise from true north for which the           PK
                                                                               antenna elevation angle is given in the data-item “elev_ang”
             elev_ang       A.7.e.2           S9(2).9            x      x      minimum elevation angle in degrees of the antenna in the azimuth given in
                                                                               data-item “azm”
             f_cmp_rec                        X                                code indicating if two records compared are equal [E], have basic             BR internal data
                                                                               differences [B], have non-basic differences [N] or the second record is not
                                                                               found [X]
e_as_stn                                                                       Associated earth station
             grp_id                           9(9)         x            x      unique identifier of the group                                                PK, FK; see NOTE 1
             seq_no                           9(4)         x            x      sequence number                                                               PK; see NOTE 1
             e_as_id                          9(9)                             identifier of associated earth station                                        BR internal data

             stn_name       C.10.b.1          X(30)        x           30A     name of the transmitting or receiving station
             stn_type       C.10.b.2          X            x            x      code indicating if the earth station is specific [S] or typical [T]. Code [P]
                                                                               indicates test points for a network (except Article 2A) subject to a plan
                                                                               and Res553
             long_dec                         S9(3).9(4)                x      longitude in degrees with four decimals                                         derived data
             lat_dec                          S9(2).9(4)                x      latitude in degrees with four decimals                                          derived data
             ant_alt                          S9(4)                     x      altitude of the earth station antenna in meters
```


---

## [Página 4]

```text
Table Name          Data Item    Items in AP4     Format      4/2   4/3    Plans                                   Description                                            Comment
             clim_zone                          X                           x      rain climatic zone
             noise_t            C.10.d.6        9(6)          x            30B     total receiving system noise temperature, expressed in kelvins referred to
                                                                                   the output of the receiving antenna
             gain               C.10.d.3        n:S9(2).9(1   x              x     maximum isotropic gain of the antenna expressed in dB
                                                )
                                                p:S9(2).9(2
                                                )
             ant_diam           C.10.d.7 /      9(3).9(4)     x           30/30A diameter of the earth station antenna (in meters) or the equivalent antenna
                                C.10.d.8                                         diameter, (i.e. the diameter, in metres, of a parabolic antenna with the
                                                                                 same off-axis performance as the receiving associated earth station
                                                                                 antenna)
             dgso               C.10.d.9        9(3).9(2)     x            30B Antenna dimension aligned with the geostationary arc (DGSO) (m)
             bmwdth             C.10.d.4        9(3).9(2)     x              x   angular width of radiation main lobe expressed in degrees with two
                                                                                 decimal positions
             pattern_id         C.10.d.5.a.1    9(9)          x              x   the key to the reference table for the co-polar antenna radiation pattern      see NOTE 4
             long_deg           C.10.c.1        9(3)          x            30A degree part of longitude coordinate of the station expressed in degrees,
                                                                                 minutes and seconds
             long_ew            C.10.c.1        X             x            30A longitude direction indicator: East [E] or West [W]
             long_min           C.10.c.1        9(2)          x            30A minute part of longitude coordinate of the station expressed in degrees,
                                                                                 minutes and seconds
             long_sec           C.10.c.1        9(2)          x            30A second part of longitude coordinate of the station expressed in degrees,
                                                                                 minutes and seconds
             lat_deg            C.10.c.1        9(2)          x            30A degree part of latitude coordinate of the station expressed in degrees,
                                                                                 minutes and seconds
             lat_ns             C.10.c.1        X             x            30A latitude direction indicator: North [N] or South [S]
             lat_min            C.10.c.1        9(2)          x            30A minute part of latitude coordinate of the station expressed in degrees,
                                                                                 minutes and seconds
             lat_sec            C.10.c.1        9(2)          x            30A second part of latitude coordinate of the station expressed in degrees,
                                                                                 minutes and seconds
             ctry               C.10.c.2        X(3)          x            30A symbol of the country or geographical area in which the Earth station is
                                                                                 located
             act_code                           X             x                  code indicating the action to be taken on the entity                           see NOTE 3
             attch_e            C.10.d.5.a.2    9(2)          x              x   number of the attachment for the co-polar radiation pattern diagram            see NOTE 2
             attch_e_x          C.10.d.5.a.2    9(2)          x              x   number of the attachment for the cross-polar antenna radiation pattern         see NOTE 2
                                                                                 diagram
             diag_e                             9(2)          x              x   number of the co-polar antenna radiation pattern diagram in GIMS
             diag_e_x                           9(2)          x              x   number of the cross-polar antenna radiation pattern diagram in GIMS
             stn_old            C.10.b          X(30)         x              x   previous name of the transmitting or receiving station                         if the associated station name
                                                                                                                                                                is to be changed
             rcp_type                           X                            x     code indicating if the reception type is individual [I] or community [C]
             pwr_max            C.8.g.1         S9(2).9(1)                         the maximum aggregate power, in dBW, of all carriers (per transponder, if
                                                                                   applicable) supplied to the input of the transmitting antenna of the
                                                                                   associated earth station
```


---

## [Página 5]

```text
Table Name       Data Item    Items in AP4       Format   4/2   4/3   Plans                                   Description                                             Comment
             bdwdth_aggr     C.8.g.2         9(8).9(2)                        the aggregate bandwidth of all carriers (per transponder, if applicable)
                                                                              supplied to the input of the transmitting antenna of the associated earth
                                                                              station
             f_trp_band      C.8.g.3         X                                an indicator showing whether the bandwidth of the transponder
                                                                              corresponds to the aggregate bandwidth of all carriers (per transponder, if
                                                                              applicable) supplied to the input of the transmitting antenna of the
                                                                              associated earth station
             f_e_change                      X            x                   For future use when antenna pattern diagrams will be in GIMS
             f_cmp_rec                       X                                code indicating if two records compared are equal [E], have basic             BR internal data
                                                                              differences [B], have non-basic differences [N] or the second record is not
                                                                              found [X]
e_srvcls                                                                      Nature of service and class of station for an associated earth station
             grp_id          BR              9(9)         x                   unique identifier of the group                                                PK, FK; see NOTE 1
             seq_e_as                        9(4)         x                   sequence number of the corresponding associated earth station                 PK, FK; see NOTE 1
             seq_no                          9(4)         x                   sequence number                                                               PK; see NOTE 1
             stn_cls         C.10.d.1        X(2)         x     x             class of station code                                                         Table 3 of the Preface
             nat_srv         C.10.d.2        X(2)         x     x             nature of service code
e_stn                        A.7                                              Earth station
             ntc_id          BR              9(9)               x             unique identifier of the notice                                               PK, FK; see NOTE 1
             stn_name        A.1.e.2         X(30)              x             name of the earth station
             long_deg        A.1.e.3.b       9(3)               x             degree part of longitude coordinate of the station expressed in degrees,
                                                                              minutes and seconds
             long_ew         A.1.e.3.b       X                  x             longitude direction indicator: East [E] or West [W]
             long_min        A.1.e.3.b       9(2)               x             minute part of longitude coordinate of the station expressed in degrees,
                                                                              minutes and seconds
             long_sec        A.1.e.3.b       9(2)               x             second part of longitude coordinate of the station expressed in degrees,
                                                                              minutes and seconds
             lat_deg         A.1.e.3.b       9(2)               x             degree part of latitude coordinate of the station expressed in degrees,
                                                                              minutes and seconds
             lat_ns          A.1.e.3.b       X                  x             latitude direction indicator: North [N] or South [S]
             lat_min         A.1.e.3.b       9(2)               x             minute part of latitude coordinate of the station expressed in degrees,
                                                                              minutes and seconds
             lat_sec         A.1.e.3.b       9(2)               x             second part of latitude coordinate of the station expressed in degrees,
                                                                              minutes and seconds
             sat_name        A.4.c.1         X(30)              x             name of the associated space station
             long_nom        A.4.c.2         S9(3).9(2)         x             nominal longitude of the associated space station, give “-” for West, “+”     in degrees from -179.99 to
                                                                              for East                                                                      +180.00
             attch_hor       A.7.a           9(2)               x             the attachment number of the earth station horizon elevation diagram          see NOTE 2
             elev_min        A.7.b.1         9(2).9             x             the planned minimum angle of elevation of the antenna’s main beam axis,
                                                                              in degrees, from the horizontal plane
             elev_max        A.7.b.2         9(2).9             x             the planned maximum angle of elevation of the antenna’s main beam axis,
                                                                              in degrees, from the horizontal plane
```


---

## [Página 6]

```text
Table Name      Data Item    Items in AP4       Format    4/2   4/3   Plans                                   Description                                           Comment
             azm_fr         A.7.c.1         9(3).9              x             value clockwise from true north for the beginning limit of an azimuthal
                                                                              sector expressed in degrees
             azm_to         A.7.c.2         9(3).9              x             value clockwise from true north for the end limit of an azimuthal sector
                                                                              expressed in degrees
             ant_alt        A.7.d           S9(5)               x             altitude of the earth station antenna
             long_dec                       S9(3).9(4)                        longitude in degrees with four decimals                                     derived data
             lat_dec                        S9(2).9(4)                        latitude in degrees with four decimals                                      derived data
emiss                                                                         Emission
             grp_id                         9(9)          x     x       x     unique identifier of the group                                              PK, FK; see NOTE 1
             seq_no                         9(4)          x     x       x     sequence number                                                             PK; see NOTE 1
             design_emi     C.7.a           X(9)          x     x       x     designation of emission                                                     In the case of AP30B this
                                                                                                                                                          item is required only for
                                                                                                                                                          submission under Article 8
             pep_max        C.8.a.1/C.8.b.1 n:S9(2).9(1   x     x     30/30A the maximum/total/mean peak envelope power [dBW]
                            /C.8.b.3.a      )
                                            p:S9(2).9(2
                                            )
             pwr_ds_max     C.8.a.2/C.8.b.2 n:S9(3).9(1   x     x       x     maximum/mean power density [dBW/Hz]
                            /C.8.b.3.b      )
                                            p:S9(3).9(2
                                            )
             pep_min        C.8.c.1         S9(2).9(1)    x     x             minimum peak envelope power delivered to the antenna [dBW]
             pwr_ds_min     C.8.c.3         S9(3).9(1)    x     x             minimum power density [dBW/Hz]
             c_to_n         C.8.e.1         S9(2).9       x     x             C/N (total, clear sky) objective
             pwr_ds_nbw     C.8.h           S9(3).9(2)                  x     power density [dBW/Hz] averaged over the necessary bandwidth
             pulse_length   C.16.a.1        9(7).9(2)     x                   the pulse length in µs                                                      for active sensors
             pulse_rep      C.16.a.2        9(6).9(5)     x                   the pulse repetition frequency in kHz                                       for active sensors
             f_emi_type     C.8.a/C.8.b     X             x                   flag indicating that it is not appropriate to identify individual carriers
                                                                              (C.8.b)
             attch_pep      C.8.c.2         9(2)          x     x             the attachment number providing the reason for absence of the minimum
                                                                              peak power
             attch_mpd      C.8.c.4         9(2)          x     x             the attachment number providing the reason for absence of the minimum
                                                                              power density
             attch_c2n      C.8.e.2         9(2)          x     x             the attachment number providing the reason for absence of the carrier-to-
                                                                              noise ratio
             pwr_ds_nbc                     S9(3).9(2)                 30B    power density [dBW/Hz] averaged over the necessary bandwidth of a
                                                                              narrow bandwidth carrier
             f_cmp_rec                      X                                 code indicating if two records compared are equal [E], have basic           BR internal data
                                                                              differences [B], have non-basic differences [N] or the second record is not
                                                                              found [X]
epfd_freq                                                                     table of frequency bands applicable for each scenario
             ntc_id         BR              9(9)          x                   unique identifier of the notice
             scen_id        BR135           9(4)          x                   unique identifier of the scenario
```


---

## [Página 7]

```text
Table Name       Data Item    Items in AP4     Format     4/2   4/3   Plans                                   Description                                           Comment
             seq_no                          9(4)         x                   sequence number                                                          PK; see NOTE 1
             emi_rcp         B.2             X            x                   code identifying a beam as either transmitting [E] or receiving [R]
             freq_min        BR137           9(7).9(6)    x                   minimum frequency, in MHz, for which the scenario applies
             freq_max        BR138           9(7).9(6)    x                   maximum frequency, in MHz, for which the scenario applies
epfd_param                                                                    information, required for space stations operating in a frequency
                                                                              band subject to Nos. 22.5C, 22.5D, 22.5F or 22.5L, to characterize
                                                                              properly the performance of the non-geostationary-satellite system
                                                                              for EPFD examination
             ntc_id          BR              9(9)         x                   unique identifier of the notice                                          PK; see NOTE 1
             scen_id         BR135           9(4)         x                   unique identifier of the scenario                                        PK
             scen_name       BR136           X(255)       x                   name of the scenario
             nbr_sat_td      A.4.b.7.a       9(9)         x                   maximum number of non-geostationary satellites receiving simultaneously
                                                                              with overlapping frequencies from the associated earth stations within a
                                                                              given cell

             density         A.4.b.7.b       9.9(12)      x                   average number of associated earth stations with overlapping frequencies
                                                                              per
                                                                              square kilometre within a cell
             avg_dist        A.4.b.7.c       9(4).9       x                   average distance, in kilometres, between co-frequency cells
             f_x_zone        A.4.b.7.d.1     X            x                   type of exclusion zone (topocentric angle [Y])
             x_zone          A.4.b.7.d.2     9(2).9       x                   width of the exclusion zone in degrees

             elev_min        A.4.b.7.cbis    S9(3).9(2)   x                   minimum elevation angle at which any associated earth station can
                                                                              transmit to or receive from a non-geostationary satellite
es_ctry                                                                       country or geographical area table for the earth station
             ntc_id          BR              9(9)         x                   unique identifier of the notice                                              PK; see NOTE 1
             ctry            A.1.e.2bis      X(3)         x                   country or geographical area in which the station is located, using the      PK
                                                                              symbols from the Preface
ex_op_grp                                                                     Exclusive operation group
             grp_id          BR              9(9)                      x      unique identifier of the group                                               PK; see NOTE 1
             beamgrp_id      C.15.a          X(6)                      x      beam group code
geo                                                                           Geostationary space station
             ntc_id          BR              9(9)         x            x      unique identifier of the notice                                              PK; see NOTE 1
             sat_name        A.1.a           X(30)        x            x      name of the space station
             long_nom        A.4.a.1         S9(3).9(2)   x            x      nominal longitude of the space station, give “-” for West “+” for East       in degrees from -179.99 to
                                                                                                                                                           +180.00
             tol_east        A.4.a.2.a       9.9(2)       x            x      value indicating the planned longitudinal tolerance East of the nominal
                                                                              longitude of the space station
             tol_west        A.4.a.2.b       9.9(2)       x            x      value indicating the planned longitudinal tolerance West of the nominal
                                                                              longitude of the space station
             inclin_exc      A.4.a.2.c       9(2).9(2)    x           30B     inclination excursion
             long_orig                       S9(3).9(2)                       original nominal longitude of the space station, give “-” for West “+” for
                                                                              East
```


---

## [Página 8]

```text
Table Name      Data Item    Items in AP4       Format   4/2   4/3    Plans                                   Description                                            Comment
gpub                        A.13                                              Publication information for a group of assigned frequencies
             grp_id                         9(9)         x     x        x     unique identifier of the group                                                 PK, FK; see NOTE 1
             seq_no                         9(4)         x     x        x     sequence number                                                                PK; see NOTE 1
             pub_ref                        X(12)        x     x        x     Symbol indicating the part of the WIC/IFIC or of the Circular Telegram or
                                                                              the Special Section of the Weekly Circular/IFIC in which the group was
                                                                              published
             pub_no                         9(4)         x     x        x     the number of the WIC/IFIC or of the Circular Telegram or of the Special
                                                                              Section of the Weekly Circular/IFIC in which the group was published
             ssn_type                       X            x     x        x     the origin of the Circular Telegram or of Special Section of the Weekly
                                                                              Circular/IFIC in which the group was published (N=filed by notifying
                                                                              administration; B=BR)
             ssn_rev                        X            x     x        x     type of revision (M, S or A)
             ssn_rev_no                     9(2)         x     x        x     revision number of special section
             wic_no                         9(4)         x     x        x     number of the WIC/IFIC in which the group was published                        BR data
             d_wic                          9(8)         x     x        x     the date of most recent publication of a list of assignments in the            BR data (date in yyyymmdd
                                                                              WIC/IFIC                                                                       format)
grp                                                                           Common data for a group of assigned frequencies
             grp_id                         9(9)         x     x       x      unique identifier of the group                                                 PK; see NOTE 1
             ntc_id                         9(9)         x     x       x      unique identifier of the notice                                                FK
             emi_rcp        B.2             X            x     x       x      code identifying a beam as either transmitting [E] or receiving [R]            FK
             beam_name      B.1.a           X(8)         x     x       x      designation of the satellite antenna beam                                      FK
             noise_t        C.5.a/C.5.c     9(6)         x     x      30A/    receiving system noise temperature or the system noise temperature at the
                                                                      30B     output of the signal processor (for active sensors)
             d_rcv          BR              9(8)                              date of receipt of the list of frequency assignments pertaining to the group BR internal data
             d_prot_eff                     9(8)                              the date from which a list of assignments is taken into account according    BR data (date in yyyymmdd
                                                                              to provisions of the RR, as appropriate                                      format)
             d_reg_limit                    9(8)         x                    regulatory limit date for bringing into use of a group of assignments        BR data (date in yyyymmdd
                                                                                                                                                           format)
             d_inuse        A.2.a/A.2.c     9(8)         x     x        x   date of bringing into use                                                      date in yyyymmdd format
             f_biu                          X            x                  code indicating if the assignments (for notification notices only i.e. Art.11, BR data (date in yyyymmdd
                                                                            AP30/30A#A5 and AP30B#A8) have been confirmed brought into use by format)
                                                                            the administration (C=confirmed; NULL=not confirmed)
             fdg_reg                        X(2)                            findings: conformity with Radio Regulations; Table 13A of the Preface to BR data
                                                                            BR IFIC (Space Services) (13A1)
             fdg_plan                       X(2)                            findings: conformity with a Plan or a Coordination Procedure; Table 13A BR data
                                                                            of the Preface to BR IFIC (Space Services) (13A2)
             fdg_tex                        X(2)                            findings: results from technical examination; Table 13A of the Preface to BR data
                                                                            BR IFIC (Space Services) (13A3)
             area_no        C.11.a          9(2)         x                  sequence number associating a particular service area diagram with the
                                                                            group
             bdwdth         C.3.a/C.5.d.2   9(8).9(2)    x     x     30/30A assigned frequency band expressed in kHz OR the bandwidth of the               In the case of AP30B this
                                                                            frequency band, in kHz, observed by the radio-astronomy station OR             item is required only for
                                                                            receiver noise bandwidth (for active sensors)                                  submission under Article 8
```


---

## [Página 9]

```text
Table Name      Data Item    Items in AP4       Format   4/2   4/3    Plans                                   Description                                           Comment
             freq_min                       9(7).9(6)                       minimum frequency in MHz (assigned frequency – half bandwidth) (of all        derived data
                                                                            frequencies for this group)
             freq_max                       9(7).9(6)                       maximum frequency in MHz (assigned frequency + half bandwidth) (of            derived data
                                                                            all frequencies for this group)
             polar_type     C.6.a           X(2)         x     x     30/30A symbol indicating the type and the direction of polarization, where           Table 5 of the Preface
                                                                            applicable (in case of circular or elliptical polarization)
             polar_ang      C.6.b           9(3).9(2)    x     x     30/30A in case of linear polarization the value of the angle (in degrees) measured   Table 5 of the Preface
                                                                            anticlockwise in a plane normal to the beam axis from the equatorial plane
                                                                            to the electric vector of the wave
             wic_no                         9(4)                            the number of the WIC/IFIC in which the list of assignments was most          BR data
                                                                            recently published
             wic_part                       X                               the part of the WIC/IFIC in which the list of assignments was most            BR data
                                                                            recently published
             d_wic                          9(8)                            the date of most recent publication of a list of assignments in the           BR data (date in yyyymmdd
                                                                            WIC/IFIC                                                                      format)
             adm_resp       A.3.b           X(2)         x     x        x   symbol identifying the responsible administration, Table 12A/12B of the       In the case of AP30B this
                                                                            Preface to BR IFIC (Space Services)                                           item is required only for
                                                                                                                                                          submission under Article 8
             op_agcy        A.3.a           9(3)         x     x        x     operating agency number, Table 12A/12B of the Preface to BR IFIC            In the case of AP30B this
                                                                              (Space Services)                                                            item is required only for
                                                                                                                                                          submission under Article 8
             d_rcv_start                    9(8)         x                  date of receipt of the corresponding notices published in API/A or            BR data (date in yyyymmdd
                                                                            AP30*/E Part A or AP30B/A6A                                                   format)
             prov                           X(12)                           provision of the RR according to which the group is submitted
             plan_status                    X(4)                      30B status of entries (either assignments = LIST or allotments = PLAN)
             reg_op_fr      A.11.a          9(4)                     30/30A start of regular hours of reception expressed in UTC
             reg_op_to      A.11.b          9(4)                     30/30A end of regular hours of reception expressed in UTC
             f_ap30b_art6   A.19.a          X                         30B a commitment that the use of assignment shall not cause unacceptable
                                                                            interference to, nor claim protection from, those assignments for which
                                                                            agreement still need to be obtained (§6.25 of Art. 6 of App 30B)
             f_cost_rec                     X                               flag to indicate that the group of frequency assignments is subject to cost   BR internal data
                                                                            recovery
             sr_nbw         C.8.b.3.c       9(6).9(2)    x                  necessary bandwidth for active sensors (MHz)
             page_no                        9(4)         x     x            page number on the paper notice

             act_code                       X            x     x        x     code indicating the action to be taken on the entity                        see NOTE 3
             prd_valid      A.2.b           9(2)         x                    period of validity in years
             remark                         X(100)       x     x              symbols used as indicated in Table No. 13C
             tgt_grp_id                     9(9)         x     x              unique identifier of the group to be modified                               see NOTE 1
             pwr_max        C.8.d.1 /       S9(2).9(1)   x                    maximum total peak envelope power in dBW or maximum aggregate
                            C.8.g.1                                           power in dBW supplied to the input of the antenna
```


---

## [Página 10]

```text
Table Name       Data Item       Items in AP4         Format   4/2   4/3   Plans                                    Description                                             Comment
             bdwdth_aggr        C.8.d.2 /         9(8).9(2)    x                   the contiguous bandwidth of the satellite transponder or the aggregate
                                C.8.g.2                                            bandwidth of all carriers (per transponder, if applicable) supplied to the
                                                                                   input of the transmitting antenna of the earth station
             f_trp_band         C.8.g.3           X                                an indicator showing whether the bandwidth of the transponder
                                                                                   corresponds to the aggregate bandwidth of all carriers (per transponder, if
                                                                                   applicable) supplied to the input of the transmitting antenna of the earth
                                                                                   station
             observ_cls         C.13.a            X(2)                             class of observation                                                           for radio astronomy
             d_upd                                9(8)                             the date of update of a list of assignments in the SNS (Master Register and    BR data (date in yyyymmdd
                                                                                   Requests for Coordination)                                                     format)
             st_cur             BR                X(2)                             the status of this frequency assignment group
             d_st_cur           BR                9(8)                             the date of entry into this status for this frequency assignment group
             fdg_observ                           X(10)                            findings: remarks concerning the findings entered in Column 13A; Table         BR data
                                                                                   13B of the Preface to BR IFIC (Space Services) (13B2)
             spl_grp_id                           9(9)                             Split group id                                                                 BR data
             comment                              X(30)                            comments                                                                       BR internal use
             elev_min           A.4.b.7.cbis /    S9(3).9(2)   x            x      minimum elevation angle at which any associated earth station can
                                C.13.c                                             transmit to a non-geostationary satellite or minimum elevation angle at
                                                                                   which the radio astronomy station conducts single-dish or VLBI
                                                                                   observations
             srv_code                             X(6)                             generic code indicating the space service type for the list of frequency
                                                                                   assignments of the group
             f_no_intfr         C.2.c             X            x     x             code indicating compliance with Article 4.4 of the Radio Regulations
             plan_categ                           X(4)                     30B     symbol indicating the category of the group of assignments or allotments       BR internal data
                                                                                   within its status
             pfd_pk_7g          B.4.b.5           S9(3).9(1)   x                   calculated peak value of power-flux density produced within +/- 5 degrees
                                                                                   inclination of the geostationary-satellite orbit
             ra_stn_type        C.13.b            X                                the type of radio-astronomy station in the frequency band shown under          for radio astronomy
                                                                                   C.3.b (S - for single dish, V - for very long baseline interferometry
                                                                                   (VLBI))
             eirp_nom           C.8.f.1/C.8.f.2   S9(2).9(1)   x                   space station's nominal equivalent isotropically radiated power(s) (e.i.r.p)   required only for a space-to-
                                                                                   on the beam axis                                                               space link
             sensitivity        C.16.b.1          9(3).9(2)    x                   sensitivity threshold, in kelvins                                              for passive sensors
             d_inuse_submitt    A.2.a             9(8)         x                   The date of bringing into use as submitted by the administration in the
             ed                                                                    first notice for recording of the assignment
             f_diff_reg_limit                     X            x                   indicator to show that a group contain assignment(s) with different            BR internal data
                                                                                   regulatory limit
             f_1143a            BR                X            x                   flag to indicate modification request under 11.43A

             d_first_ntf                          9(8)                             date of the first notification                                                 BR data (date in yyyymmdd
                                                                                                                                                                  format)
```


---

## [Página 11]

```text
Table Name      Data Item     Items in AP4       Format   4/2   4/3   Plans                                   Description                                             Comment
             f_no_comment                    X                                flag to indicate whether commenting period is re-open. Flag is empty in        BR internal data
                                                                              normal case (commenting period is opened) and set to ‘Y’ when the
                                                                              period is not reopened.

             f_no_coord                      X            x     x             flag to indicate that the group is not subject to coordination (BR internal
                                                                              use only)
             f_nfd_lnk        BR             X            x                   indicator that the group is for use in accordance with Resolution 163/164
                                                                              in the 14.5-14.8 GHz band (not for feeder link for the BSS)
             prv_pub_grp_id                  9(9)                             group ID of the corresponding group (with EC/TC class of station) in Part BR internal data
                                                                              II-S (to be used in the Bureau’s examination under resolves 1.1.5 of
                                                                              Resolution 169 (WRC-19))

             f_sa_change                     X                                code indicating that the service area diagram has been modified

             f_fdg_reqd                      X                                code indicating if finding is required                                         BR internal data
             cmp_grp_id                      9(9)                             grp_id of the second group if two groups are compared                          BR internal data
             f_cmp_str                       X                                code indicating if two structures compared are equal [E], have basic           BR internal data
                                                                              differences [B], have non-basic differences [N] or the second structure is
                                                                              not found [X]
             f_cmp_rec                       X                                code indicating if two records compared are equal [E], have basic              BR internal data
                                                                              differences [B], have non-basic differences [N] or the second record is not
                                                                              found [X]
             f_cmp_freq                      X                                code indicating if two lists of frequencies compared are equal [E], have       BR internal data
                                                                              basic differences [B], have non-basic differences [N] or the second list of
                                                                              records is not found [X]
             f_cmp_emi                       X                                code indicating if two lists of emissions compared are equal [E], have         BR internal data
                                                                              basic differences [B], have non-basic differences [N] or the second list of
                                                                              records is not found [X]
             f_cmp_eas                       X                                code indicating if two lists of associated earth stations compared are equal   BR internal data
                                                                              [E], have basic differences [B], have non-basic differences [N] or the
                                                                              second list of records is not found [X]
             f_cmp_prov                      X                                code indicating if two lists of provisions compared are equal [E], have        BR internal data
                                                                              basic differences [B], have non-basic differences [N] or the second list of
                                                                              provisions is not found [X]
             f_cmp_sas                       X                                code indicating if two lists of associated space stations compared are equal   BR internal data
                                                                              [E], have basic differences [B], have non-basic differences [N] or the
                                                                              second list of records is not found [X]
             f_cmp_gpub                      X                                code indicating if two lists of notified publications compared are equal       BR internal data
                                                                              [E], have basic differences [B], have non-basic differences [N] or the
                                                                              second list of records is not found [X]
             f_cmp_fdg                       X                                code indicating if two lists of finding references compared are equal [E],     BR internal data
                                                                              have basic differences [B], have non-basic differences [N] or the second
                                                                              list of records is not found [X]
grp_lnk                                                                       Group link
```


---

## [Página 12]

```text
Table Name      Data Item    Items in AP4       Format   4/2   4/3    Plans                                Description                                         Comment
             grp_id                         9(9)                              unique identifier of the grp                                           PK
             lnk_grp_id                     9(9)                              unique identifier of the linked grp                                    PK
             ntc_id                         9(9)                              unique identifier of the notice
             lnk_ntc_id                     9(9)                              unique identifier of the linked notice
             ntf_rsn                        X                                 notification reason - see "notice" table
             lnk_ntf_rsn                    X                                 notification reason of the linked notice. Refer to ‘notice’ table.
grp_res35                                                                     Information for a group of assigned frequencies under Resolution 35
             grp_id         BR              9(9)                              unique identifier of the group                                         PK; FK
             ms_step                        X(2)         x                    RES 35 milestone indicator                                             BR internal data
             f_ms_met                       X            x                    RES 35 milestone criteria indicator                                    BR internal data
             d_ms_next_dead                 9(8)         x                    Deadline of the next RES35 milestone                                   BR internal data
             line
hor_elev                                                                   Horizon elevation diagram                                                    see NOTE 2
             ntc_id                         9(9)               x           unique identifier of the notice                                              PK, FK; see NOTE 1
             azm            A.7.a           9(3).9             x           azimuth in degrees measured clockwise from true north for which the          PK
                                                                           horizon elevation is given in the data-item “elev_ang”
             elev_ang       A.7.a.1         S9(2).9            x           elevation angle in degrees of the horizon in the azimuth given in data-item
                                                                           “azm”
             hor_dist       A.7.a.2         9(2).9             x           distance in km from the earth station to the horizon in the azimuth given in
                                                                           data-item “azm”
             f_cmp_rec                      X                              code indicating if two records compared are equal [E], have basic            BR internal data
                                                                           differences [B], have non-basic differences [N] or the second record is not
                                                                           found [X]
interf_poc                                                                 point of contact table for the purpose of tracing cases of unacceptable
                                                                           interference from non-GSO ESIMs
             grp_id         BR              9(9)         x                 identification of the group                                                  PK
             poc_id         A.33.a/A.36.c   9(9)         x           AP30B identifier of the point of contact                                           PK
                                                                     ESIM
isl_param                                                                  inter-satellite link parameters table
             ntc_id         BR              9(9)         x                 unique identifier of the notice                                              PK, FK; see NOTE 1
             freq_min       BR              9(7).9(6)    x                 minimum frequency, in MHz, for which the mask pattern and the                PK
                                                                           exclusion zone angle apply
             freq_max       BR              9(7).9(6)    x                 maximum frequency, in MHz, for which the mask pattern and the                PK
                                                                           exclusion zone angle apply
             x_zone_isl     A.27.d          9(2).9       x                 exclusion zone angle in degrees
             mask_id_isl    A.27.e          9(4)         x                 mask pattern identifier
mask_info                                                                  Mask information
             ntc_id                         9(9)         x                 unique identifier of the notice                                              PK, FK
             mask_id        A.14.a.1 /      9(9)         x                 unique identifier of the mask                                                PK
                            A.14.b.1 /
                            A.14.c.1
```


---

## [Página 13]

```text
Table Name      Data Item    Items in AP4       Format   4/2   4/3    Plans                                     Description                                              Comment
             freq_min       A.14.a.2 /      9(7).9(6)    x                    the lowest frequency for which the mask is valid [GHz]
                            A.14.b.2 /
                            A.14.c.2
             freq_max       A.14.a.3 /      9(7).9(6)    x                    the highest frequency for which the mask is valid [GHz]
                            A.14.b.3 /
                            A.14.c.3
             f_mask                         X            x                    flag indicating if the mask type is eirp for the space station [S], eirp for the
                                                                              associated earth station [E] or pfd at the space station [P]
             f_mask_type                    X            x                    flag indicating the type of the pfd mask

mask_lnk1                                                                     Link between mask, group and satellite of a non-geostationary system
             ntc_id                         9(9)         x                    unique identifier of the notice                                                    PK
             scen_id                        9(4)         x                    unique identifier of the scenario                                                  PK
             seq_no                         9(4)         x                    sequence number of the mask                                                        PK
             orb_id                         9(4)         x                    sequence number of the orbital plane                                               FK
             sat_orb_id                     9(9)         x                    sequence number of the satellite in the orbital plane                              FK
             mask_id        A.14.a.1 /      9(9)         x                    unique identifier of the mask                                                      FK
                            A.14.b.1 /
                            A.14.c.1
mask_lnk2                                                                     Link between mask, associated earth station and satellite of a non-
                                                                              geostationary system
             ntc_id                         9(9)         x                    unique identifier of the notice                                                    PK
             scen_id                        9(4)         x                    unique identifier of the scenario                                                  PK
             seq_no                         9(4)         x                    sequence number of the mask                                                        PK
             e_as_id                        9(9)         x                    sequence number of the associated earth station                                    FK
             orb_id                         9(4)         x                    sequence number of the orbital plane                                               FK
             sat_orb_id                     9(9)         x                    sequence number of the satellite in the orbital plane                              FK
             mask_id        A.14.a.1 /      9(9)         x                    unique identifier of the mask                                                      FK
                            A.14.b.1 /
                            A.14.c.1
mask_lnk3                                                                   System operating parameters identification for Rec. S.1503-3
             ntc_id                         9(9)         x                  Unique identifier of the notice                                                      PK, FK; see NOTE 1
             param_id                       9(4)         x                  Unique identifier of the system operating parameters                                 PK
mod_char                                                                    General characteristics of the emission
             grp_id                         9(9)                            unique identifier of the group                                                       PK, FK; see NOTE 1
             seq_emiss                      9(4)                            sequence number of the characteristics                                               PK; see NOTE 1
             i_mod_typ      C.9.a.1         9(4)                        x   the type of modulation
             freq_low       C.9.a.2.a       9(6).9(6)                       the lowest frequency of the baseband
             freq_hi        C.9.a.2.b       9(6).9(6)                       the highest frequency of the baseband
             freq_dev       C.9.a.2.c       9(6).9(6)                       the r.m.s. frequency deviation of the pre-emphasized signal for a test tone
                                                                            as a function of baseband frequency
             freq_dev_tv    C.9.a.3.a       9(6).9(6)                30/30A the peak-to-peak frequency deviation of the pre-emphasized signal
                                                                            (television)
```


---

## [Página 14]

```text
Table Name      Data Item        Items in AP4     Format    4/2   4/3    Plans                                   Description                                         Comment
             i_pre_emph         C.9.a.3.b       9(4)                    30/30A the pre-emphasis characteristics for a carrier frequency modulated by a
                                                                               television signal (TV)
             i_mplx_typ         C.9.a.3.c       9(4)                    30/30A the characteristics of the multiplexing of the video signal with sound
                                                                               signal(s) or other signal(s) (TV)
             bit_rate           C.9.a.4.a       9(4)                           the bit rate for a carrier phase-shift modulated by a digital signal
             nbr_phase          C.9.a.4.b       9(4)                           the number of phases for a carrier phase-shift modulated by a digital
                                                                               signal
             attch_sig          C.9.a.5.a       9(4)                           number of the attachment indicating the nature of modulating signal for an
                                                                               amplitude modulated carrier
             ampl_mod           C.9.a.5.b       X                              the kind of amplitude modulation used
             freq_dev_fm        C.9.a.6.a       9(6).9(6)               30/30A the peak-to-peak frequency deviation, in MHz, of the energy dispersal
                                                                               waveform for frequency modulation
             freq_swp           C.9.a.6.b       9(6).9(6)               30/30A the sweep frequency (kHz) of the energy dispersal waveform
             i_nrgy_dsp         C.9.a.6.c       9(4)                    30/30A the energy dispersal waveform
             i_nrgy_dsp_typ     C.9.a.7         9(4)                       x   the type of energy dispersal, if other forms of modulation than FM are
                                                                               used
             attch_mod          C.9.a.8         9(4)                           attachment indicating for all other types of modulation, such particulars as
                                                                               may be useful for an interference study
             i_tv_sys           C.9.a.9         9(4)                           TV system
             i_sound_bc         C.9.b.1         9(4)                    30/30A sound broadcasting characteristics for analogue carriers
             i_baseband         C.9.b.2         9(4)                    30/30A the composition of the baseband for an analogue carrier
             range_agc          A.12            9(3).9(2)                30A the range of automatic gain control, in dB
ngeo_diag_                                                                     table of diagrams/attachments provided to the group (for non-NGSO
grp                                                                            only)
             grp_id             BR              9(9)        x                  identification of the group                                                  PK; see NOTE 1
             attch_multi_acce   C.9.c.1         9(4)        x                  type of multiple access (attachment number)
             ss
             attch_spec_mask    C.9.c.2         9(4)        x                    spectrum mask (attachment number)
             diag_spec_mask     C.9.c.2         9(4)        x                    spectrum mask (GIMS diagram number)
             attch_aff_rgn      C.11.b          9(4)        x                    appropriate information required to calculate the affected region
                                                                                 (attachment number)
             diag_aff_rgn       C.11.b          9(4)        x                    appropriate information required to calculate the affected region (GIMS
                                                                                 diagram number)
ngma                                                                             Link-noise/transmission gain for one or more straps
             ntc_id                             9(9)        x                    unique identifier of the notice                                             PK, FK; see NOTE 1
             ngma_id            D.2             9(4)        x                    identifier for a given set of equivalent satellite link noise temperature   PK; see NOTE 1
                                                                                 (ESLNT) and transmission gain values (gamma)
             act_code           D.2             X           x                    code indicating the action to be taken on the entity                        see NOTE 3
             strp_id_fr         D.2             9(4)        x                    lower limit of the range of strap serial numbers
             strp_id_to         D.2             9(4)        x                    upper limit of the range of strap serial numbers
             noise_t_lo         D.2.a.1         9(8)        x                    lowest value of equivalent satellite link noise temperature (ESLNT)
                                                                                 associated with the strap
```


---

## [Página 15]

```text
Table Name       Data Item     Items in AP4       Format   4/2   4/3   Plans                                   Description                                            Comment
             gain_as_lo       D.2.a.2         S9(2).9(1)   x                   value of transmission gain (gamma) associated with the value of ESLNT
                                                                               given above
             noise_t_hr       D.2.b.1         9(8)         x                   value of equivalent satellite link noise temperature for highest ratio of
                                                                               transmission gain to ESLNT associated with the strap
             gain_as_hr       D.2.b.2         S9(2).9(1)   x                   value of transmission gain (gamma) associated with the value of ESLNT
                                                                               given above
             stn_name         D.2             X(30)        x                   name of the receiving earth station
             f_cmp_rec                        X                                code indicating if two records compared are equal [E], have basic             BR internal data
                                                                               differences [B], have non-basic differences [N] or the second record is not
                                                                               found [X]
non_geo                                                                        Non-geostationary space station
             ntc_id                           9(9)         x                   unique identifier of the notice                                               PK, FK; see NOTE 1
             sat_name         A.1.a           X(30)        x                   name of the satellite
             ref_body         A.4.b.1         X            x                   code for the reference body about which the satellite orbits                  Table 8 of the Preface
             nbr_sat_nh       A.4.b.3.e.1     9(9)         x                   the maximum number of space stations in the non-geostationary-satellite
                                                                               system simultaneously transmitting on a co-frequency basis on the
                                                                               Northern Hemisphere
             nbr_sat_sh       A.4.b.3.e.2     9(9)         x                   the maximum number of space stations in the non-geostationary-satellite
                                                                               system simultaneously transmitting on a co-frequency basis on the
                                                                               Southern Hemisphere
             nbr_plane        A.4.b.2         9(4)         x                   number of non-geostationary orbital planes
             nbr_sat_td       A.4.b.7.a       9(9)         x                   maximum number of co-frequency tracked non-geostationary satellites
                                                                               receiving simultaneously
             density          A.4.b.7.b       9.9(12)      x                   average number of associated earth stations transmitting with overlapping
                                                                               frequencies per km² in a cell
             avg_dist         A.4.b.7.c       9(4).9       x                   average distance between co-frequency cells in kilometers
             f_x_zone         A.4.b.7.d.1     X            x                   flag indicating the type of zone: if the exclusion zone angle is the angle
                                                                               alpha [Y] or the angle X [N]
             x_zone           A.4.b.7.d.2     9(2).9       x                   width of the exclusion zone in degrees
             f_sdm            A.1.g           X            x                   indicator showing that the non-geostationary-satellite system is planned to
                                                                               be operated in accordance with Resolution 32
             f_constell       A.4.b.3.a       X            x                   indicator of whether the non-geostationary-satellite system represents a
                                                                               "constellation"
             multi_config_typ A.4.b.3.b       X            x                   indicator of whether all the orbital planes identified under A.4.b.1 describe
             e                                                                 a) a single configuration where all frequency assignments to the satellite
                                                                               system will be in use [S] or b) multiple configurations that are mutually
                                                                               exclusive [M]
             nbr_config       A.4.b.3.c       9(2)         x                   the number of sub-sets of orbital characteristics that are mutually
                                                                               exclusive
             examset_type     A.4.b.6bis      X            x                   indicator showing whether the set of operating parameters is limited set
                                                                               [L] or extended set [E]
notice                                                                         General information for the notice
             ntc_id                           9(9)         x     x      x      unique identifier of the notice                                               PK; see NOTE 1
```


---

## [Página 16]

```text
Table Name      Data Item    Items in AP4       Format   4/2   4/3   Plans                                     Description                                             Comment
             prov                           X(12)        x     x      x      provision of the RR according to which the notice is submitted
             plan_id                        X(4)                             identifier of the plan                                                      BR internal use
             adm            A.1.f.1         X(3)         x     x      x      country symbol of the notifying administration
             ntwk_org                       X(3)         x     x      x      symbol of the organization operating regional or international satellite
                                                                             networks (Table 2 of the Preface to BR IFIC (Space Services))
             ntf_occurs                     X            x     x      x      code indicating if the notice was intended for first [F] submission or for
                                                                             satellite in non-planned bands, one of the following types of resubmission:
                                                                             [S] Resubmission of a Satellite Network with no coordination status
                                                                             update
                                                                             [A] Resubmission of a Satellite Network with coordination status update
                                                                             with affected administrations only
                                                                             [M] Resubmission of a Satellite Network (GSO only) with coordination
                                                                             status update with affected administrations and List of affected satellite
                                                                             networks
                                                                             [L] Resubmission of a Satellite Network (GSO only) with indication of
                                                                             the List of coordination status of affected satellite networks only
                                                                             or [R] for all other types of resubmission (e.g. earth stations, cases not
                                                                             covered by above).

                                                                             For Article 4 of Appendices 30 and 30A, the code [A] indicates a
                                                                             proposed addition/modification to the Plan/List, [P] indicates entered into
                                                                             the relevant Plan/List, [Q] indicates existing system, [R] indicates
                                                                             provisionally entered in the Plan/List, [V] indicates a pending network
                                                                             under coordination
             ntf_rsn                        X                                code indicating that the notice has been submitted under RR1488 [N],             derived data
                                                                             RR1060 [C], RR1107 [D], 9.1 [A], 9.6 [C], 9.7A [C], 9.17 [D], 9.21 [C],
                                                                             11.2 [N], 11.12 [N], AP30/30A-Articles 2A & 4 [B], AP30/30A-Article 5
                                                                             [N], AP30B-Articles 6 & 7 [P], AP30B-Article 8 [N] or Res49 [U]
             st_cur                         X(2)                             processing status of the notice                                                  BR internal use
             f_aa_type                      X                                flag indicating assignment/allotment type (plan/list, etc.)                      BR data
             act_code                       X            x     x      x      code indicating the action to be taken on the entity                             see NOTE 3
             d_rcv                          9(8)                             date of receipt of the notice                                                    BR data (date in yyyymmdd
                                                                                                                                                              format)
             wic_no                         9(4)                             the number of the WIC/IFIC in which the notice was most recently                 BR data
                                                                             published
             wic_part                       X                                the part of the WIC/IFIC in which the notice was most recently published         BR data
             d_wic                          9(8)                             the date of most recent publication of the notice in the WIC/IFIC                BR data (date in yyyymmdd
                                                                                                                                                              format)
             f_adm_proxi    A.1.f.2         X            x            x      flag indicating that administration is notifying on behalf of other
                                                                             administrations
             ntc_type                       X            x     x      x      code indicating if the notice is of a geostationary satellite [G], non-
                                                                             geostationary satellite [N], specific earth station [S], typical earth station
                                                                             [T] or radio astronomy station [R]
```


---

## [Página 17]

```text
Table Name      Data Item   Items in AP4       Format   4/2   4/3   Plans                                     Description                                           Comment
             adm_ref_id                    X(20)        x     x      x      reference identifier of the notice given by the notifying administration      not mandatory, not used by
                                                                                                                                                          BR
             d_adm                         9(8)         x     x      x      the date of the notice given by the notifying administration                  not mandatory, not used by
                                                                                                                                                          BR
             tgt_ntc_id                    9(9)         x     x      x      identifier of the notice to be modified or suppressed                         see NOTE 1
             f_int_ext                     X                                code indicating if the notice is internal [I], external [E], administration   BR internal use
                                                                            withdrawal [W], BR withdrawal [Z] or resubmitted [R]
             d_st_cur                      9(8)                             date of entry of the notice into the current processing status                BR internal use (date in
                                                                                                                                                          yyyymmdd format)
             st_prv                        X(2)                             previous processing status of the notice                                      BR internal use
             d_upd                         9(8)                             the date of update of a notice in the SNS                                     BR internal use (date in
                                                                                                                                                          yyyymmdd format)
             f_basic                       X                                code indicating basic modifications                                           BR internal use
             f_spl                         X                                code indicating if the notice was split                                       BR internal use
             spl_ntc_id                    9(9)                             identifier of the notice created as a result of the split                     BR internal use
             ntwk_pack                     X(4)                             network package identifier
             f_mod_type                    X                                flag used to indicate that the filing was created using Wizards provided in
                                                                            SpaceCap (API, DBIU, RS49)
             f_val_cat                     X                                Flag indicating validation category                                           BR internal data
             cmp_ntc_id                    9(9)                             code indicating the ntc_id of the second network/earth station beam if two
                                                                            networks/earth stations are compared
             f_cmp_str                     X                                code indicating if two structures compared are equal [E], have basic          BR internal data
                                                                            differences [B], have non-basic differences [N] or the second structure is
                                                                            not found [X]
             f_cmp_rec                     X                                code indicating if two records compared are equal [E], have basic             BR internal data
                                                                            differences [B], have non-basic differences [N] or the second record is not
                                                                            found [X]
             f_cmp_orb                     X                                code indicating if two lists of orbit records compared are equal [E], have    BR internal data
                                                                            basic differences [B], have non-basic differences [N] or the second list of
                                                                            records is not found [X]
             f_cmp_strp                    X                                code indicating if two lists of straps compared are equal [E], have basic     BR internal data
                                                                            differences [B], have non-basic differences [N] or the second list of
                                                                            records is not found [X]
             f_cmp_ngma                    X                                code indicating if two lists of noise-gamma records compared are equal        BR internal data
                                                                            [E], have basic differences [B], have non-basic differences [N] or the
                                                                            second list of records is not found [X]
             f_cmp_hori                    X                                code indicating if two lists of horizon elevation records compared are        BR internal data
                                                                            equal [E], have basic differences [B], have non-basic differences [N] or
                                                                            the second list of records is not found [X]
             f_cmp_elev                    X                                code indicating if two lists of antenna elevation records compared are        BR internal data
                                                                            equal [E], have basic differences [B], have non-basic differences [N] or
                                                                            the second list of records is not found [X]
```


---

## [Página 18]

```text
Table Name       Data Item    Items in AP4       Format   4/2   4/3   Plans                                    Description                                             Comment
             f_cmp_pfd                       X                                code indicating if two lists of pfd compliance records compared are equal BR internal data
                                                                              [E], have basic differences [B], have non-basic differences [N] or the
                                                                              second list of records is not found [X]
             f_cmp_oper                      X                                code indicating if two lists of non-geostationary satellite records compared BR internal data
                                                                              are equal [E], have basic differences [B], have non-basic differences [N]
                                                                              or the second list of records is not found [X]
             f_cfex                          X                                code indicating the result of check for existing processing                     BR internal data
             f_val                           X                                code indicating the result of validation processing                             BR internal data
             f_mod                           X                                code indicating that data was modified                                          BR internal data
             prov_desc                       X(50)                            additional information to specify the exact provision
             f_partial_sup                   X                                flag to indicate partial suppression                                            BR internal data
ntc_commit                                                                    Commitments
             ntc_id                          9(9)         x     x             unique identifier of the notice                                                 PK, FK; see NOTE 1
             commit_type                     X(30)        x     x             commitment type code                                                            PK; see NOTE 7
             attch_no                        9(4)         x     x             attachment number corresponding to the commitment label in
                                                                              commit_type, if required
ntc_memo                                                                      Comments / Remarks (Resolution 49 and API only)
             ntc_id                          9(9)                             unique identifier of the notice                                                 PK
             adm_remark                      X(255)                           remarks made by the administration
             br_comment                      X(255)                           BR comments
orbit                                                                         Orbital plane of a non-geostationary satellite
             ntc_id          BR              9(9)         x                   unique identifier of the notice                                                 PK; see NOTE 1
             orb_id                          9(4)         x                   sequence number of the orbital plane                                            PK
             nbr_sat_pl      A.4.b.4.b       9(9)         x                   number of satellites per non-geostationary orbital plane
             act_code                        X            x                   Action code for orbital plane which is subject to addition, modification, or see NOTE 3
                                                                              suppression
             orbit_set_id    A.4.b.3.d       9(4)         x                   Identifier of the orbital configuration subset to which this orbital plane
                                                                              belongs
             right_asc       A.4.b.4.g       9(3).9(2)    x                   angular separation in degrees between the ascending node and the vernal         if 9.11A applies
                                                                              equinox
             inclin_ang      A.4.b.4.a       9(3).9(2)    x                   inclination angle of the satellite orbit with respect to the Earth’s equatorial
                                                                              plane
             prd_ddd         A.4.b.4.c.1     9(3)         x                   day part of time elapsing between two consecutive passages of a non-
                                                                              geostationary satellite through a point in its orbit
             prd_hh          A.4.b.4.c.2     9(2)         x                   hour part of time elapsing between two consecutive passages of a non-
                                                                              geostationary satellite through a point in its orbit
             prd_mm          A.4.b.4.c.3     9(2)         x                   minute part of the time elapsing between two consecutive passages of a
                                                                              non-geostationary satellite through a point in its orbit
             apog_km         A.4.b.4.d       9(5).9(2)    x                   the farthest altitude of the non-geostationary satellite above the surface of distances > 99999 km are
                                                                              the Earth or other reference body - expressed in kilometers                     expressed as a product of the
                                                                                                                                                              values of the fields “apogee”
                                                                                                                                                              and “apog_exp” (see below)
                                                                                                                                                              e.g.: 125 000 =1.25*10e5
```


---

## [Página 19]

```text
Table Name       Data Item    Items in AP4       Format   4/2   4/3   Plans                                    Description                                             Comment
             perig_km        A.4.b.4.e       9(5).9(2)    x                   the nearest altitude of the non-geostationary satellite above the surface of   distances > 99999 km are
                                                                              the Earth or other reference body – expressed in kilometers                    expressed as a product of the
                                                                                                                                                             values of the fields “perigee”
                                                                                                                                                             and “perig_exp” (see below)
                                                                                                                                                             e.g.: 125 000 =1.25*10e5
             perig_arg       A.4.b.4.i       9(3).9       x                   angular separation (in degrees) between the ascending node and the             If 9.11A applies
                                                                              perigee of an elliptical orbit.
             op_ht_km        A.4.b.4.f       9(5).9(2)    x                   minimum altitude of the space station above the surface of the Earth at        distances > 99999 km are
                                                                              which any satellite transmits                                                  expressed as a product of the
                                                                                                                                                             values of the fields “op_ht”
                                                                                                                                                             and “op_ht_exp” (see below)
                                                                                                                                                             e.g.: 125 000 =1.25*10e5
             f_stn_keep      A.4.b.6.c       X            x                   flag indicating if the space station uses [Y] or does not use [N] station-
                                                                              keeping to maintain a repeating ground track
             f_alt_keep      A.4.b.4.p       X            x                   indicator (Y/N) of whether the space station uses station-keeping to
                                                                              maintain the altitudes of the apogee and perigee during its operational
                                                                              lifetime
             attch_alt       A.4.b.4.q       9(2)         x                   altitude of the apogee and perigee in kilometers (attachment number)
             apog_dist       A.4.b.4.r       9(5).9(2)    x                   distance to the apogee of the space station in kilometers
             perig_dist      A.4.b.4.s       9(5).9(2)    x                   distance to the perigee of the space station in kilometers
             rpt_prd_dd      A.4.b.6.d       9(3)         x                   day part of constellation repeat period (s)
             rpt_prd_hh      A.4.b.6.d       9(2)         x                   hour part of constellation repeat period (s)
             rpt_prd_mm      A.4.b.6.d       9(2)         x                   minute part of constellation repeat period (s)
             rpt_prd_ss      A.4.b.6.d       9(2)         x                   second part of constellation repeat period (s)
             f_precess       A.4.b.6.e       X            x                   flag indicating if the space station should [Y] or should not [N] be
                                                                              modeled with specific precession rate of the ascending node of the orbit
                                                                              instead of the J2 term
             precession      A.4.b.6.f       9(3).9(2)    x                   for a space station that is to be modeled with specific precession rate of
                                                                              the ascending node of the orbit instead of the J2 term, the precession rate
                                                                              in degrees/day measured counter-clockwise in the equatorial plane
             long_asc        A.4.b.4.j       9(3).9(2)    x                   longitude of the ascending node for the jth orbital plane measured counter-
                                                                              clockwise in the equatorial plane from the Greenwich meridian to the
                                                                              point where the satellite orbit makes its south-north crossing of the
                                                                              equatorial plane (0° <=Θj < 360°)
             keep_rnge       A.4.b.6.j       9(2).9       x                   longitudinal tolerance of the longitude of the ascending node
             f_sunsynch      A.4.b.4.m       X            x                   indicator of whether the space station uses sun-synchronous orbit or not
             lt_type         A.4.b.4.n       X            x                   indicator of whether the space station references the local time of the
                                                                              ascending node [A] or the descending node [D]
             lt_ref          A.4.b.4.o       9(6)         x                   the local time of the ascending or descending (per A.4.b.n) node in
                                                                              hhmmss format
             f_inuse         BR              X            x                   Identification of the orbital plane number under Nos 11.44C,D
             f_cmp_rec                       X                                code indicating if two records compared are equal [E], have basic           BR internal data
                                                                              differences [B], have non-basic differences [N] or the second record is not
                                                                              found [X]
```


---

## [Página 20]

```text
Table Name      Data Item      Items in AP4       Format   4/2   4/3   Plans                                    Description                                              Comment
             f_cmp_pha                        X                                 code indicating if two lists of phase records compared are equal [E], have      BR internal data
                                                                                basic differences [B], have non-basic differences [N] or the second list is
                                                                                not found [X]
orbit_lnk                                                                       table to link a non-geostationary space station antenna with the
                                                                                orbital plane
             ntc_id                           9(9)         x                    unique identifier of the notice                                                 PK, FK; see NOTE 1
             emi_rcp          B.2             X            x                    code identifying a beam as either transmitting [E] or receiving [R]             PK, FK
             beam_name        B.1.a           X(8)         x                    designation of the satellite antenna beam                                       PK, FK
             orb_id           B.4.a.1         9(4)         x                    identifying sequence number of the orbital plane                                PK, FK
             f_all_sat                        X            x                    code indicating that the beam operates with all satellites in the orbital
                                                                                plane
orbit_set                                                                       orbit set table
             ntc_id           BR              9(9)         x                    unique identifier of the notice                                                 PK; see NOTE 1
             orbit_set_id     A.4.b.3.d       9(4)         x                    if the orbital planes identified under A.4.b.3.b describe multiple mutually     PK
                                                                                exclusive configurations, identification of the orbital planes’ id numbers
                                                                                that are associated with each of the mutually exclusive configurations
             aggr_eirp_msspr A.26.a           S9(2).9(1)   x                    maximum aggregate e.i.r.p. in a 1 MHz reference bandwidth of associated
             ot                                                                 non-GSO earth stations operating co-frequency of a single non-GSO
                                                                                constellation/configuration towards any point within the geostationary arc
             aggr_pfd_msspro A.26.b           S9(3).9(1)   x                    maximum aggregate pfd in a 1 MHz reference bandwidth caused by all
             t                                                                  non-GSO space stations operating co-frequency towards the same location
                                                                                in a single non-GSO constellation/configuration at any point of the Earth’s
                                                                                surface within the visibility area of the GSO
             f_x_zone_msspr   A.26.c          X            x                    for the exclusion zone about the geostationary-satellite orbit, the type of
             ot                                                                 zone (topocentric angle [Y], satellite-based angle [N])
             x_zone_mssprot   A.26.d          9(2).9       x                    for the exclusion zone about the geostationary
                                                                                -satellite orbit, if the zone is based on a topocentric angle or a satellite-
                                                                                based angle, the width of the zone, in degrees
ovrl_epm                                                                        Overall equivalent protection margin – Appendix 30/30A Region 2
             grp_id_up                        9(9)                     30/30A   unique identifier of the group uplink                                           PK, FK; see NOTE 1
             grp_id                           9(9)                     30/30A   unique identifier of the group downlink                                         PK, FK
             seq_e_as_dn                      9(4)                     30/30A   sequence number of the earth associated station                                 PK, FK
             seq_asn_up                       9(4)                     30/30A   sequence number of the frequency assignment uplink                              PK, FK
             seq_asn_dn                       9(4)                     30/30A   sequence number of the frequency assignment downlink                            PK, FK
             seq_emi_up                       9(4)                     30/30A   sequence number of the emission uplink                                          PK, FK
             seq_emi_dn                       9(4)                     30/30A   sequence number of the emission downlink                                        PK, FK
             oepm                             S9(3).9(3)               30/30A   overall equivalent protection margin in dB
phase                                                                           Initial phase angle of a non-geostationary satellite in an orbital plane
             ntc_id                           9(9)         x                    unique identifier of the notice                                                 PK; see NOTE 1
             orb_id                           9(4)         x                    sequence number of the orbital plane                                            PK
             orb_sat_id                       9(9)         x                    satellite sequence number in the orbital plane                                  PK
             itu_sat_id       BR134           X(30)        x                    unique identification of each satellite
             act_code                         X            x                    code indicating the action to be taken on the entity
```


---

## [Página 21]

```text
Table Name      Data Item      Items in AP4     Format    4/2   4/3   Plans                                  Description                                           Comment
             phase_ang        A.4.b.4.h       9(3).9(2)   x                   initial phase angle of the satellite in the orbital plane                   if 9.11A applies
             f_res8           BR              X           x                   flag to indicate satellite linked to beams and orbit subject to RES8
             d_discontinued   BR              9(8)        x                   date of when the use of the satellite is discontinued (RES8)
             f_cmp_rec                        X                               code indicating if two records compared are equal [E], have basic           BR internal data
                                                                              differences [B], have non-basic differences [N] or the second record is not
                                                                              found [X]
pl_strap                                                                      Connection between uplink and downlink beams/frequencies (plans)
                                                                              30/30A for Region 2 and for Plan 30B
             ntc_id                           9(9)                     x      unique identifier of the notice                                             PK
             freq_dn          D.1.a.4         9(6).9(5)                x      assigned frequency of the downlink forming part of the strap                PK
             freq_up          D.1.a.3         9(6).9(5)                x      assigned frequency of the uplink forming part of the strap                  PK
             grp_id_dn                        9(9)                     x      unique identifier of the downlink group forming part of the strap           PK
             grp_id_up                        9(9)                     x      unique identifier of the uplink group forming part of the strap             PK
             pbeam_name                       X(8)                     x      designation of the satellite antenna beam (plan)
             multibeam_set                    9(4)                     x      Multibeam code
             exop_set         C.15.a          9(4)                     x      Exclusive operation code
             f_victim_op                      X                        x      'Y' for old historical victims, not mentioned in the RR (no relation with
                                                                              Art.6 part A), 'N' for the rest. (No 'new' victims are expected to be added
                                                                              in the future.)
             agg_tolerance                    9.9(2)                   x      0.05 dB for assignments stemming from conversion without modification
                                                                              or conversion with modification which is within the envelope
                                                                              characteristics of the initial allotment; NULL for the rest (BR software
                                                                              will apply 0.05 dB for assignments in the Plan and 0.25 dB for others)
provn                                                                         coordination information
             grp_id                           9(9)        x     x             unique identifier of the group                                              PK, FK; see NOTE 1
             coord_prov       A5/A6           X(20)       x     x             reference to provision of the RR, Appendix or Resolution                    PK; see NOTE 1
             agree_st                         X           x     x             code indicating if the coordination or agreement has been obtained [O] or PK
                                                                              requested [R]
             seq_no                           9(4)        x     x             sequence number                                                             PK
             coord_st                         X                               code indicating the result of the coordination process
             adm                              X(3)        x     x             country symbol of the notifying administration                              Table 1A of the Preface
             ntwk_org                         X(3)        x     x             symbol of the organization operating regional or international satellite
                                                                              networks (Table 2 of the Preface to BR IFIC (Space Services))
             ctry                             X(3)                            country or geographical area
             f_no_comment                     X                               flag to indicate whether commenting period is re-opened. Flag is empty in BR internal data
                                                                              normal case (commenting period is opened) and set to 'Y' when the period
                                                                              is not reopened.
pwr_ctrl                                                                      Power control information
             grp_id                           9(9)                            unique identifier of the group                                              PK, FK; see NOTE 1
             seq_assgn                        9(4)                            sequence number of the frequency assignment                                 PK, FK; see NOTE 1
             seq_emiss                        9(4)                            sequence number of the emission                                             PK, FK; see NOTE 1
             pwr_ctrl         C.8.i           9(4).9                  30A     power control
```


---

## [Página 22]

```text
Table Name       Data Item       Items in AP4     Format     4/2   4/3   Plans                                   Description                                           Comment
res35_deplo                                                                      RES 35 deployment summary
y
              ntc_id            BR              9(9)                             unique identifier of the RES 35 notice. Refer to ‘res35_notice’ table       PK;FK see NOTE 1
              freq_notif_id     BR              9(4)                             unique identifier of the frequency band                                     PK
              nbr_sat_pl        A.4.b.4.b       9(9)                             number of satellites per notified orbital plane
              freq_min_mhz                      9(7).9(6)                        lower bound of the frequency range for the deployed satellites
              freq_max_mhz                      9(7).9(6)                        upper bound of the frequency range for the deployed satellites
              nbr_sat_deploye   RES35.A1.A5     9(9)                             total number of space stations deployed into each notified orbital plane
              d                                                                  and frequency range
              nbr_sat_min                       9(9)                             minimum number of deployed space stations to meet the current
                                                                                 Milestone
              ms_findings                       X(6)                             milestone reached indicator
res35_freq                                                                       RES 35 space station frequency characteristics
              ntc_id            BR              9(9)                             unique identifier of the RES 35 notice. Refer to ‘res35_notice’ table       PK;FK see NOTE 1
              launch_id                         9(4)                             unique identifier of the RES 35 launch. Refer to ‘res35_launch’ table       PK;FK
              station_id                        9(9)                             unique identifier of the RES 35 space station. Refer to ‘res35_sp_stn’      PK;FK
                                                                                 table
              freq_id                           9(4)                             unique identifier of the RES 35 space station transmit or receive frequency PK
                                                                                 range
              freq_min_mhz      RES35.A1.C1     9(7).9(6)                        lower bound of the frequency range for the space station transmit or
                                                                                 receive frequency range
              freq_max_mhz      RES35.A1.C1     9(7).9(6)                        upper bound of the frequency range for the space station transmit or
                                                                                 receive frequency range
res35_launc                                                                      RES 35 launch information
h
              ntc_id            BR              9(9)                             unique identifier of the RES 35 notice. Refer to ‘res35_notice’ table        PK;FK see NOTE 1
              launch_id                         9(4)                             unique identifier of the RES 35 launch                                       PK
              lsp_name          RES35.A1.B1     X(20)                            name of the launch vehicle provider
              vehicle           RES35.A1.B2     X(20)                            name of the launch vehicle
              facility          RES35.A1.B3     X(20)                            name of the launch facility
              ctry                              X(3)                             symbol of the country or geographical area in which the launch facility is   derived data
                                                                                 located
              long_dec          RES35.A1.B3     S9(3).9(4)                       longitude coordinate of the launch facility in degrees                       derived data
              lat_dec           RES35.A1.B3     S9(2).9(4)                       latitude coordinate of the launch facility in degrees                        derived data
              d_launch          RES35.A1.B4     9(8)                             launch date
res35_sp_st                                                                      RES 35 space station characteristics
n
              ntc_id            BR              9(9)                             unique identifier of the RES 35 notice. Refer to ‘res35_notice’ table        PK;FK see NOTE 1
              launch_id                         9(4)                             unique identifier of the RES 35 launch. Refer to ‘res35_launch’ table        PK;FK
              station_id                        9(9)                             unique identifier of the RES 35 space station                                PK
              itu_sat_id        RES35.A1.A6     X(30)        x                   unique identification of each satellite
              sp_stn_name       RES35.A1.C3     X(30)                            name of the space station
```


---

## [Página 23]

```text
Table Name       Data Item     Items in AP4     Format     4/2   4/3   Plans                                    Description                                        Comment
              apog_km         RES35.A1.C2     9(5).9(2)                        orbital characteristics of the space station (altitude of the apogee in
                                                                               kilometers)
              perig_km        RES35.A1.C2     9(5).9(2)                        orbital characteristics of the space station (altitude of the perigee in
                                                                               kilometers)
              inclin_ang      RES35.A1.C2     9(3).9(2)                        orbital characteristics of the space station (inclination in degrees)
              perig_arg       RES35.A1.C2     9(3).9                           orbital characteristics of the space station (argument of the perigee in   If 9.11A applies
                                                                               degrees)
res35_sp_st                                                                    res35 space station deployment link table
n_deploy
              ntc_id          BR              9(9)                             unique identifier of the notice                                            PK; see NOTE 1
              launch_id       BR              9(4)                             unique identifier of the RES 35 launch. Refer to ‘res35_launch’ table      PK
              station_id      BR              9(9)                             unique identifier of the RES 35 space station. Refer to ‘res35_sp_stn’     PK
                                                                               table
              freq_notif_id   BR              9(4)                             unique identifier of the frequency range. Refer to 'res35_deploy' table    PK
res49_sel                                                                      Resolution 49 download table                                               data downloaded from SNS
                                                                                                                                                          for filing RS49
              grp_id                          9(9)                             group id number                                                            PK; see NOTE 1
              sat_name        A.1.a           X(30)                            name of the space station
              long_nom        A.4.a.1         S9(3).9(2)                       nominal longitude of space station
              ntf_rsn                         X                                notification reason - see "notice" table
              adm             A.1.f.1         X(3)                             notifying administration
              ntwk_org        A.1.f.3         X(3)                             intergovernmental satellite organization
              d_inuse         A.2.a           9(8)                             date of bringing into use
              ntc_id                          9(9)                             BR notice id of the filing
              st_cur                          X(2)                             processing status of the filing
              d_prot_eff                      9(8)                             date of protection of the frequency group
              freq_min                        9(7).9(6)                        lower bound of the frequency range for the group
              freq_max                        9(7).9(6)                        upper bound of the frequency range for the group
              wic_no                          9(4)                             IFIC publication number of the group
              d_wic                           9(8)                             date of the IFIC publication
              act_code                        X                                action-code
              emi_rcp         B.2             X                                satellite beam emission/reception code
              beam_name       B.1.a           X(8)                             satellite beam designation
              ntc_type                        X                                type of notice indicator (G, N)
              d_reg_g                         9(8)                             end of the regulatory period                                               based on API filing or d_rev
                                                                                                                                                          field in table fdg_rev
res8_sp_stn                                                                    table of submitted and observed orbital values
              ntc_id          BR              9(9)                             unique identifier of the notice                                            PK; see NOTE 1
              itu_sat_id      BR              X(30)                            unique identifier of each satellite                                        PK
              sp_stn_name     RES8.A1.B1      X(30)                            name of the space station
              obs_phase_ang   RES8.A1.B2      9(3).9(2)                        observed initial phase angle of the satellite in the orbital plane
              apog_dist_obs   RES8.A1.B3      9(5).9(2)                        observed distance to the apogee of the space station in kilometers
```


---

## [Página 24]

```text
Table Name       Data Item     Items in AP4       Format    4/2   4/3   Plans                                     Description                                                Comment
             perig_dist_obs   RES8.A1.B3      9(5).9(2)                          observed distance to the perigee of the space station in kilometers
             inclin_ang_obs   RES8.A1.B3      9(3).9(2)                          observed inclination angle of the satellite orbit with respect to the Earth’s
                                                                                 equatorial plane, in degrees
             apog_dist        A.4.b.4.r       9(5).9(2)                          distance to the apogee of the space station in kilometers (at the date of
                                                                                 RES 8 submission)
             perig_dist       A.4.b.4.s       9(5).9(2)                          distance to the perigee of the space station in kilometers (at the date of
                                                                                 RES 8 submission)
             inclin_ang       A.4.b.4.a       9(3).9(2)                          inclination angle of the satellite orbit with respect to the Earth’s equatorial
                                                                                 plane, in degrees (at the date of RES 8 submission)
             res8_resolves                    X(12)                              resolves number (Possible values: resolves6 / resolves7 / resolves9 /
                                                                                 empty)
s_as_stn                                                                         Space associated station
             grp_id                           9(9)          x                    unique identifier of the group                                                    PK, FK; see NOTE 1
             sat_name         C.10.a.1        X(30)         x                    name of the associated space station                                              PK
             beam_name                        X(8)          x                    designation of the associated satellite antenna beam                              PK
             act_code                         X             x                    code indicating the action to be taken on the entity                              see NOTE 3
             stn_type         C.10            X             x                    type of the associated space station: geostationary [G] or non-
                                                                                 geostationary [N]
             long_nom         C.10.a.2        S9(3).9(2)    x                    nominal longitude of the associated space station, if geostationary; give “-      in degrees from -179.99 to
                                                                                 ” for West “+” for East                                                           +180.00
             f_cmp_rec                        X                                  code indicating if two records compared are equal [E], have basic                 BR internal data
                                                                                 differences [B], have non-basic differences [N] or the second record is not
                                                                                 found [X]
s_beam                                                                           satellite antenna beam
             ntc_id                           9(9)          x             x      unique identifier of the notice                                                   PK, FK; see NOTE 1
             emi_rcp          B.2             X             x             x      code identifying a beam as either transmitting [E] or receiving [R]               PK
             beam_name        B.1.a           X(8)          x             x      designation of the satellite antenna beam                                         PK
             beam_old                         X(8)          x                    previous designation of the satellite antenna beam                                if the designation of the
                                                                                                                                                                   beam is to be changed
             f_steer          B.1.c           X             x                    code indicating if the beam is steerable (see No. 1.191) or reconfigurable
             gain             B.3.a.1         n:S9(2).9(1   x             x      maximum isotropic gain of the antenna expressed in dB; copolar gain for
                                              )                                  plans
                                              p:S9(2).9(2
                                              )
             gain_x           B.3.a.2         9(2).9(2)                 30/30A   crosspolar gain (for shaped beams only)
             beamlet                          9(2).9                       x     spot beam
             bore_long        B.3.f.1.a       S9(3).9(2)                   x     longitude coordinate of the satellite boresight
             bore_lat         B.3.f.1.b       S9(2).9(2)                   x     latitude coordinate of the satellite boresight
             maj_axis         B.3.f.2.c       9(2).9(2)                    x     major axis of the satellite beam projection
             min_axis         B.3.f.2.d       9(2).9(2)                    x     minor axis of the satellite beam projection
             orient           B.3.f.2.b       S9(3).9(2)                   x     orientation of the satellite beam
             pnt_acc          B.3.d           9.9(2)        x                    the pointing accuracy of the antenna, in degrees
             rot_acc          B.3.f.2.a       9.9(2)                      x      satellite beam rotational accuracy
```


---

## [Página 25]

```text
Table Name      Data Item       Items in AP4       Format   4/2   4/3   Plans                                  Description                                             Comment
             pattern_id       B.3.c.1.b        9(9)                             unique identifier of the co-polar radiation pattern in the reference table
                                                                                ant_type
             freq_min                          9(7).9(6)                        minimum frequency in MHz (assigned frequency - half bandwidth) (of all       derived data
                                                                                frequencies for this beam)
             freq_max                          9(7).9(6)                        maximum frequency in MHz (assigned frequency + half bandwidth) (of           derived data
                                                                                all frequencies for this beam)
             sr_type          B.1.d.1          X            x                   symbol indicating the type of the sensor A - active, P - passive
             act_code                          X            x                   code indicating the action to be taken on the entity                         see NOTE 3
             ang_alpha        B.4.a.3.a.1      9(3).9       x                   satellite beam orientation                                                   if 9.11A applies
             ang_beta         B.4.a.3.a.2      9(2).9       x                   satellite beam orientation                                                   if 9.11A applies
             attch_alpha_beta                  9(2)         x                   number of the attachment for explanation when angle alpha or angle beta
                                                                                cannot be provided
             attch_e          B.3.c.1.a        9(2)         x            x      number of the attachment for the co-polar antenna radiation pattern          see NOTE 2
                                                                                diagram
             pwr_max_4k       B.4.b.4.a        S9(2).9(1)   x                   maximum peak E.I.R.P. at 4kHz                                                if 9.11A applies
             pwr_avg_4k       B.4.b.4.b        S9(2).9(1)   x                   average peak E.I.R.P. at 4kHz                                                if 9.11A applies
             pwr_max_1m       B.4.b.4.c        S9(2).9(1)   x                   maximum peak E.I.R.P. at 1MHz                                                if 9.11A applies
             pwr_avg_1m       B.4.b.4.d        S9(2).9(1)   x                   average peak E.I.R.P. at 1MHz                                                if 9.11A applies
             prot_ratio       C.12.a           9(3).9(2)                30B     minimum acceptable aggregate C/I ratio, if less than 26 dB or 23 dB for
                                                                                submissions received by the Bureau as of 5 July 2003
             f_fdg_reqd                        X                                code indicating if finding is required                                       BR internal data
             f_tx_vis         B.2.a.1          X            x                   an indicator specifying whether the space station only transmits when
                                                                                visible from the notified service area
             tx_ang_min       B.2.a.2          9(2).9       x                   in case of non-continuous transmission in item B.2bis.a, the minimum
                                                                                elevation angle above which transmissions occur when the space station is
                                                                                visible from the notified service area
             attch_pfd_steer B.3.b.1           9(2)         x                   attachment number for method required in ROP 21.16
             f_pfd_steer_defa B.3.b.1          X            x                   Code indicating if applicable PFD limit will be met by applying the
             ult                                                                method in Annex 1 of ROP 21.16
                                                                                Y – Yes
                                                                                N – Yes but with another method described in an attachment
                                                                                X – No

             f_co_change                       X                                code indicating that the antenna gain contour diagram has been modified

             f_aggso_change                    X                                code indicating that the antenna gain towards GSO orbit diagram has been
                                                                                modified

             f_e_change                        X            x                   For future use when antenna pattern diagrams will be in GIMS
             cmp_ntc_id                        9(9)                             code indicating the ntc_id of the second network/earth station beam if two BR internal data
                                                                                networks/earth stations are compared
             cmp_beam                          X(8)                             beam_name of the second beam if two beams are compared                     BR internal data
```


---

## [Página 26]

```text
Table Name       Data Item     Items in AP4       Format    4/2   4/3   Plans                                   Description                                            Comment
              f_cmp_str                       X                                 code indicating if two structures compared are equal [E], have basic          BR internal data
                                                                                differences [B], have non-basic differences [N] or the second structure is
                                                                                not found [X]
              f_cmp_rec                       X                                 code indicating if two records compared are equal [E], have basic             BR internal data
                                                                                differences [B], have non-basic differences [N] or the second record is not
                                                                                found [X]
sat_lnk                                                                         Table to link a non-geostationary space station antenna with the
                                                                                satellite
              ntc_id                          9(9)          x                   unique identifier of the notice                                               PK, FK; see NOTE 1
              emi_rcp         B.2             X             x                   code identifying a beam as either transmitting [E] or receiving [R]           PK, FK
              beam_name       B.1.a           X(8)          x                   designation of the satellite antenna beam                                     PK, FK
              orb_id          B.4.a.1         9(4)          x                   identifying sequence number of the orbital plane                              PK, FK
              orb_sat_id      B.4.a.2         9(9)          x                   satellite sequence number in the non-geostationary orbital plane              PK, FK
sat_oper                                                                        Non-geostationary satellites with overlapping frequencies
              ntc_id                          9(9)          x                   unique identifier of the notice                                               PK, FK; see NOTE 1
              scen_id         BR              9(4)          x                   unique identifier of the scenario
              lat_fr          A.4.b.6.a.2     S9(2).9(3)    x                   lower limit of the latitude range                                             in degrees; PK
              lat_to          A.4.b.6.a.3     S9(2).9(3)    x                   upper limit of the latitude range                                             in degrees; PK
              nbr_op_sat      A.4.b.6.a.1     9(9)          x                   maximum number of non-geostationary satellites transmitting with
                                                                                overlapping frequencies to a given location within the latitude range
scraft_cmr_                                                                     frequency band(s) present on board the spacecraft
freq
              itu_scraft_id                   9(9)          x                   Unique identification of the spacecraft                                       PK, FK
              seq_no                          9(4)          x                   sequence number for this itu_scraft_id                                        PK
              freq_min                        k:9(5).9(3)   x                   start frequency in a range
                                              m:9(5).9(6)
                                              g:9(4).9(9)
              freq_max                        k:9(5).9(3)   x                   end frequency in a range
                                              m:9(5).9(6)
                                              g:9(4).9(9)
              freq_sym                        X             x                   frequency symbol
scraft_cmr_                                                                     table to identify spacecraft under Res. 552
syst
              itu_scraft_id                   9(9)          x                   Unique identification of the spacecraft                                       PK, FK
              ntwk_name                       X(30)         x                   commercial name of the satellite
              lsp_name                        X(20)         x                   name of the launch service provider
              vehicle                         X(20)         x                   name of the launch vehicle
              d_exe                           9(8)          x                   date of execution of the launch contract
              facility                        X(20)         x                   name of the launch facility
              mfct_name                       X(20)         x                   name of the manufacturer
              nbr_sat                         9(9)          x                   number of satellites procured
              d_exe_m                         9(8)          x                   date of execution of the contract
              d_deliv                         9(8)          x                   delivery date
```


---

## [Página 27]

```text
Table Name      Data Item     Items in AP4     Format      4/2   4/3   Plans                                  Description                                            Comment
             d_launch                        9(8)          x                   launch date
srv_area                                                                       Service area
             grp_id                          9(9)          x            x      identification of the group                                                 PK, FK; see NOTE 1
             ctry            C.11.a          X(3)          x            x      symbol of the country or geographical area                                  PK, FK
             f_excl_api                      X             x                   Indicator for whether the code in ctry is meant to be included (N or empty) BR internal data
                                                                               or excluded from (Y), the service area, for API only
srv_cls                                                                        Nature of service and class of station for the group of frequency
                                                                               assignments
             grp_id                          9(9)          x     x      x      identification of the group                                                 PK, FK; see NOTE 1
             seq_no                          9(4)          x     x      x      sequence number                                                             PK; see NOTE 1
             stn_cls         C.4.a           X(2)          x     x      x      class of station                                                            Table 3 of the Preface
             nat_srv         C.4.b           X(2)          x     x             nature of service                                                           Table 4 of the Preface
srvlnk_sat                                                                     table for service links
             ntc_id          BR              9(9)          x                   unique identifier of the notice                                             PK; see NOTE 1
             srvlnk_sat_name A.1.c           X(30)         x                   if different from A.1.a, the identity of the satellite network or system    PK
                                                                               containing the service link frequency assignments
strap                        D.1                                               Connection between uplink and downlink beams/frequencies
             ntc_id                          9(9)          x                   unique identifier of the notice                                             PK, FK; see NOTE 1
             strp_id                         9(4)          x                   serial number of the strap                                                  PK
             act_code        D.1             X             x                   code indicating the action to be taken on the entity                        see NOTE 3
             beam_up         D.1.a.1.a       X(8)          x                   designation of the satellite receiving antenna beam associated with the
                                                                               uplink frequency
             beam_dn         D.1.a.2.a       X(8)          x                   designation of the satellite transmitting antenna beam associated with the
                                                                               downlink frequency
             freq_symup      D.1.a.1.b       X             x                   symbol indicating kilohertz [K], megahertz [M] or gigahertz [G]
             freq_up         D.1.a.1.b       k:9(5).9(3)   x                   assigned frequency of the uplink forming part of the strap
                                             m:9(5).9(6)
                                             g:9(4).9(9)
             freq_symdn      D.1.a.2.b       X             x                   symbol indicating kilohertz [K], megahertz [M] or gigahertz [G]
             freq_dn         D.1.a.2.b       k:9(5).9(3)   x                   assigned frequency of the downlink forming part of the strap
                                             m:9(5).9(6)
                                             g:9(4).9(9)
             f_cmp_rec                       X                                 code indicating if two records compared are equal [E], have basic           BR internal data
                                                                               differences [B], have non-basic differences [N] or the second record is not
                                                                               found [X]
```


---

## [Página 28]

```text
                                                                                   BR Data

  Table Name        Data Item          Format                                                    Description                                                      Comment
all_aff_ntw                                      Table of affected/affecting networks at group level
                aff_rec_id          9(9)         unique identifier of an affected/affecting network                                                      PK
                aff_ntc_id          9(9)         unique identifier of the notice affected/affecting                                                      FK
                coord_prov          X(20)        reference to provision of the RR, Appendix or Resolution
                agree_st            X            code indicating if the coordination requirement has been identified using the arc concept [A] or ΔT/T
                                                 calculation [T]
                adm                 X(3)         country symbol of the notifying administration
                ntwk_org            X(3)         symbol of the organization operating regional or international networks (Table 2 of the Preface BR
                                                 IFIC (Space Services))
                sat_name            X(30)        name of the space station
                long_nom            S9(3).9(2)   nominal longitude of the space station, give “-” for West “+” for East                                  in degrees from -179.99 to
                                                                                                                                                         +180.00
                ntf_rsn             X            notification reason - see "notice" table
                st_aff              X(2)         processing status of the network affected/affecting                                                     BR internal use
                f_cause             X            code indicating that the network has been identified as causing [C] interference
                f_rec               X            code indicating that the network has been identified as receiving [R] interference
ap30b_ref_agg                                    Ref. aggregate C/I values
                grp_id_dn           9(9)         unique identifier of the group downlink                                                                 PK
                grp_id_up           9(9)         unique identifier of the group uplink                                                                   PK
                seq_pt              9(4)         test point sequential number                                                                            PK
                freq_band           X(8)         "12/11", "13/10", "13/11", "12/10", "12/0", "13/0", "0/10", "0/11", "6/4", "6/0", "0/4"                 PK
                c2i                 S9(3).9(6)   reference aggregated C/I value for this test point
ap30b_ref_se                                     Ref. Single Entry C/I values
                grp_id_a            9(9)         unique identifier of the affected group                                                                 PK
                grp_id_i            9(9)         unique identifier of the interferer group                                                               PK
                seq_pt              9(4)         test point sequential number                                                                            PK
                freq_band           X(8)         "12", "13", "10", "11", "12-13", "10-11", "4", "6"                                                      PK
                emi_rcp             X            'E' for emission, 'R' for reception
                c2i                 S9(3).9(6)   reference S.E. C/I value for this test point
                agree_st            X            implicitly agreed value (M) or explicitely (E)
ap30b_tr_res                                     AP30B Annex 4 findings at the notice level                                                              PK
                ntc_id              9(9)         unique identifier of the analyzed network                                                               PK
                freq_band           X(12)        "6/4", "12-13/10-11"                                                                                    PK
                ntc_id_a            9(9)         unique identifier of the affected network                                                               PK
                plan_status_a       X(4)         Status of entries of a network considered to be affected (either assignment = LIST or allotment =       PK
                                                 PLAN)
                se_dn_tp_degr_max   9(3).9(3)    maximum downlink single-entry C/I degradation on test points
                se_dn_gp_degr_max   9(3).9(3)    maximum downlink single-entry C/I degradation on grid points
                se_up_degr_max      9(3).9(3)    maximum uplink single-entry C/I degradation
                agg_degr_max        9(3).9(3)    maximum aggregate C/I degradation
```


---

## [Página 29]

```text
 Table Name        Data Item      Format                                                      Description                                                   Comment
              pfd_exc_dn_max   9(4).9(3)    maximum pfd excess in the downlink
              pfd_exc_up_max   9(4).9(3)    maximum pfd excess in the uplink
              f_pfd_appl       X            flag indicating if Pfd criteria is applicable or not
beam_tr                                     Beam information                                                                                        SNS/SPS <---> Plans
                                                                                                                                                    translation
              ant_diam         9(3).9(4)    antenna diameter                                                                                        PK
              pattern_id       9(9)         unique identifier of the antenna radiation pattern                                                      PK
              design_emi       X(9)         designation of emission                                                                                 PK
              grp_id           9(9)         unique identifier of the group                                                                          PK
              pbeam_name       X(8)         designation of the satellite antenna beam (plan)                                                        PK
              beam_name        X(8)         designation of the satellite antenna beam
              emi_rcp          X            code identifying a beam as either transmitting [E] or receiving [R]
              ntc_id           9(9)         unique identifier of the notice
fdg_ref                                     Finding reference
              grp_id           9(9)         unique identifier of the group                                                                          PK, FK; see NOTE 1
              seq_no           9(4)         sequence number                                                                                         PK; see NOTE 1
              d_fdg_rev        9(8)         date relating to the type in d_type                                                                     see NOTE 5
              d_type           X            type describing the action associated to the date in d_fdg_rev                                          see NOTE 5
              fdg_prov         X(20)        reference to a provision, appendix or resolution (including those indicated in Table 13B1 of Preface)
grp_aff_rec                                 Table to link incoming group and affected/affecting network

              grp_id           9(9)         unique identifier of the group                                                                          PK;FK
              aff_rec_id       9(9)         unique identifier of an affected/affecting network                                                      PK;FK
link_epm                                    Equivalent protection margin (link) – Appendix 30/30A Regions 1 and 3
              grp_id           9(9)         unique identifier of the group                                                                          PK; see NOTE 1
              seq_e_as         9(4)         sequence number of the earth associated station                                                         PK; see NOTE 1
              seq_assgn        9(4)         sequence number of the frequency assignment                                                             PK; see NOTE 1
              seq_emiss        9(4)         sequence number of the emission                                                                         PK; see NOTE 1
              epm              S9(3).9(3)   equivalent protection margin
ntc_lnk                                     Notice link
              ntc_id           9(9)         unique identifier of the notice                                                                         PK; see NOTE 1
              lnk_ntc_id       9(9)         unique identifier of the linked notice                                                                  PK; see NOTE 1
              ntf_rsn          X            notification reason - see ntf_rsn of "notice" table
              lnkntf_rsn       X            notification reason of the linked notice. Refer to ‘notice’ table
ntc_lnk_ref                                 Notice link reference
              plan_id          X(4)         identifier of the space plan
              ntc_id           9(9)         unique identifier of the notice
              pbeam_name       X(8)         designation of the satellite antenna beam (plan)
              adm              X(3)         country symbol of the notifying administration
              long_nom         S9(3).9(2)   nominal longitude of the space station, give '-' for West '+' for East
plan_pub                                    Publication information for plan notices
              ntc_id           9(9)         unique identifier of the notice                                                                         PK, FK; see NOTE 1
```


---

## [Página 30]

```text
 Table Name         Data Item        Format                                                   Description                                                        Comment
                wic_no            9(4)        WIC/IFIC number                                                                                           PK
                plan_pub_type     9(2)        code indicating plan publication type                                                                     PK
pub_ssn                                       Publication information for a notice
                ntc_id            9(9)        unique identifier of the notice                                                                           PK, FK; see NOTE 1
                seq_no            9(4)        sequence number                                                                                           PK; see NOTE 1
                ssn_ref           X(12)       symbol indicating the Special Section of the Weekly Circular / IFIC
                ssn_no            9(4)        number of the Special Section
                ssn_rev           X           type of revision (M, C or A)
                ssn_rev_no        9(2)        revision number of special section
sat_sys_provn                                 coordination information for the notices submitted under Article 4 of AP30/30A belonging to the
                                              same cluster in Region 2
                plan_id           X(4)        identifier of the space plan
                ntwk_pack         X(4)        network package identifier
                coord_prov        X(20)       reference to provision of the RR, Appendix or Resolution
                agree_st          X           code indicating the type of the coordination or agreement requirement – (Preface Tables 11A, 11B)
                ific_no           9(4)        the number of the IFIC in which the list of assignments was most recently published
                adm               X(3)        country symbol of the notifying administration
                ntwk_org          X(3)        symbol of the organization operation regional or international satellite networks (Table 2 of the Preface
                                              to BR IFIC (Space Services))
sps_results                                   Space plan results
                ntc_id            9(9)        unique identifier of the space plan transaction                                                           PK; see NOTE 1
                ntwk_pack         X(4)        network package identifier
                ntc_id_aff        9(9)        unique identifier of the affected transaction                                                             PK
                pbeam_name        X(8)        plan/list beam identification                                                                             PK
                aff_ch_pfd        X(56)       list of affected channels identified using PFD criterion (Regions 1 and 3 downlink only)
                pfd_exc_max       9(3).9(2)   maximum pfd excess value (Regions 1 and 3 downlink only) in dB(W/(m².27 MHz))
                aff_ch_epm        X(56)       list of affected channels identified using EPM/OEPM criterion
                epm_c2i_dgr_max   9(3).9(3)   EPM/OEPM (BSS)degradation max.
                aff_chs           X(56)       final list of channels identified as affected                                                             PK
                pfd_exc           9(3).9(2)   maximum pfd excess value for the final list of affected channels in dB(W/(m².27 MHz))
                epm_dgr           9(3).9(3)   maximum EPM/OEPM (BSS) degradation for the final list of affected channels
                freq_band         X(4)        identifier of frequency band for Regions 1 and 3 feeder-link Plan/List in 14 or 17 GHz
tr_aff_ntw                                    Affected/affecting networks for the transaction
                ntc_id            9(9)        unique identifier of the notice                                                                           FK; see NOTE 1
                coord_prov        X(20)       reference to provision of the RR, Appendix or Resolution                                                  see NOTE 6
                agree_st          X           code indicating if the coordination requirement has been identified using the arc concept [A] or ΔT/T
                                              calculation [T] or Frequency overlap [F or Q]
                aff_ntc_id        9(9)        unique identifier of the notice affected/affecting                                                        FK; see NOTE 1
                adm               X(3)        country symbol of the notifying administration
                ntwk_org          X(3)        symbol of the organization operating regional or international networks (Table 2 of the Preface to BR
                                              IFIC (Space Services))
                sat_name          X(30)       name of the space station
```


---

## [Página 31]

```text
 Table Name        Data Item     Format                                                   Description                                                            Comment
              long_nom         S9(3).9(2)   nominal longitude of the space station, give “-” for West “+” for East                                      in degrees from -179.99 to
                                                                                                                                                        +180.00
              ntf_rsn          X            notification reason - see "notice" table
              coord_st         X            code indicating status of coordination
              st_aff           X(2)         processing status of the network affected/affecting                                                         BR internal use
              f_cause          X            code indicating that the network has been identified as causing [C] interference
              f_rec            X            code indicating that the network has been identified as receiving [R] interference
              d_prot_inc       9(8)         date of protection of the frequency group (incoming network)
              wic_no           9(4)         the number of the WIC/IFIC in which the notice was most recently published                                  BR data
tr_provn                                    Coordination information for the transaction
              ntc_id           9(9)         unique identifier of the notice                                                                             PK, FK; see NOTE 1
              coord_prov       X(20)        reference to provision of the RR, Appendix or Resolution                                                    PK
              agree_st         X            code indicating if the coordination or agreement has been obtained [O] or requested [R]                     PK
              wic_no           9(4)         the number of the WIC/IFIC in which the list of assignments was most recently published                     PK
              seq_no           9(4)         sequence number                                                                                             PK; see NOTE 1
              coord_st         X            code indicating status of coordination
              adm              X(3)         country symbol of the notifying administration
              ntwk_org         X(3)         symbol of the organization operating regional or international satellite networks (Table 2 of the Preface
                                            to BR IFIC (Space Services))
              ctry             X(3)         symbol indicating geographical area
              f_pfd_lim_a40a   X            commitment to respect the power-flux density limits specified under § 4.1.13bis of Article 4 of
                                            Appendix 30/30A or § 6.15quat of Article 6 of Appendix 30B, as appropriate
```


---

## [Página 32]

```text
                                                                          Reference Tables

 Table Name        Data Item       Format                                                   Description                                                         Comment
ant_type                                    Antenna type information
               pattern_id      9(9)         unique identifier of the antenna radiation pattern                                                           PK
               f_ant_type      X            flag indicating the type of the antenna radiation pattern E - earth, S - space, A - associated earth, R -
                                            radioastronomy, P - plan space, T - plan test point
               f_sub_type      X            code indicating that antenna pattern is valid for certain types of notice or other status: B: BSS plan, C:
                                            Composite, F: FSS plan, O: obsolete, W: withdrawn
               emi_rcp         X            code identifying a beam as either transmitting [E] or receiving [R]
               pattern         X(12)        antenna radiation pattern indicated by a reference to the appropriate ITU-R Recommendation
               coefa           9(2).9       coefficient A for non-standard antenna                                                                       see NOTE 4
               coefb           9(2).9       coefficient B for non-standard antenna                                                                       see NOTE 4
               coefc           9(2).9       coefficient C for non-standard antenna                                                                       see NOTE 4
               coefd           9(2).9       coefficient D for non-standard antenna                                                                       see NOTE 4
               phi1            9(2).9       coefficient PHI1 for non-standard antenna                                                                    see NOTE 4
               f_ant_new       X            flag indicating a new antenna radiation pattern
               apl_name        X(12)        name in the antenna pattern library for this pattern
               d_upd           9(8)         date of data creation or most recent data update
ras_ant_type                                radio astronomy antenna type information table
               pattern_id      9(9)         unique identifier of the radio astronomy antenna radiation pattern
               pattern_type    X(50)        antenna type
               ant_dim         9(3).9(2)    antenna diameter
               eff_area        9(6).9(2)    effective area
               add_desc        X(500)       additional description
               d_upd           9(8)         date of data creation or most recent data update
```


---

## [Página 33]

```text
                                                                           BR Internal Data

  Table Name        Data Item     Format                                                     Description                                                             Comment
alloc_id                                     Identifier allocation                                                                                          BR internal use
               ntc_year         9(2)         year of submission of the notice                                                                               PK
               grp_id_last      9(9)         Last allocated grp_id
cmr_history                                  Spacecraft history table
               ntc_id           9(9)         unique identifier of the notice                                                                                PK, FK; see NOTE 1
               itu_scraft_id    9(9)         unique identifier of the spacecraft                                                                            PK, FK
               seq_no           9(4)         sequence number                                                                                                PK, FK
               reg_st           X            code indicating regulatory status (F = First bringing into use, S = Suspended, R= Resumed)
               d_reg_st         9(8)         Date of first bringing into use / suspending / resuming
               rsn_susp         X(255)       reason for suspension
               wic_no           9(4)         the number of the WIC/IFIC in which the notice was most recently published                                     BR data
com_el                                       Common elements                                                                                                BR internal use
               ntc_id           9(9)         unique identifier of the notice                                                                                PK, FK see NOTE 1
               prov             X(12)        provision of the RR according to which the notice is submitted
               plan_id          X(4)         identifier of the plan                                                                                         FK
               adm              X(3)         country symbol of the notifying administration
               ntwk_org         X(3)         symbol of the organization operating regional or international satellite networks (Table 2 of the Preface
                                             to BR IFIC (Space Services))
               sat_name         X(30)        name of the space station
               long_nom         S9(3).9(2)   nominal longitude of the space station, give “-” for West, “+” for East                                        in degrees from -179.99 to
                                                                                                                                                            +180.00
               act_code         X            code indicating action to be taken on the entity                                                               see NOTE 3
               ntf_rsn          X            notification reason - see "notice" table                                                                       derived data
               st_cur           X(2)         processing status of the notice                                                                                BR internal use
               d_rcv            9(8)         date of receipt of the notice                                                                                  BR data (date in yyyymmdd
                                                                                                                                                            format)
               wic_no           9(4)         the number of the WIC/IFIC in which the notice was most recently published                                     BR data
               wic_part         X            the part of the WIC/IFIC in which the notice was most recently published                                       BR data
               ntc_type         X            code indicating if the notice is of a geostationary satellite [G], non-geostationary satellite [N], specific
                                             earth station [S], typical earth station [T] or radio astronomy station [R]
               adm_ref_id       X(20)        reference identifier of the notice given by the notifying administration
               tgt_ntc_id       9(9)         identifier of the notice to be modified or suppressed
               stn_name         X(30)        name of the earth station
               long_dec         S9(3).9(4)   longitude coordinate of the earth station in degrees                                                           derived data
               lat_dec          S9(2).9(4)   latitude coordinate of the earth station in degrees                                                            derived data
               ctry             X(3)         symbol of the country or geographical area in which the station is located
               prov_desc        X(50)        additional information to specify the exact provision
freq                                         Frequency                                                                                                      BR internal use
               ntc_id           9(9)         unique identifier of the notice                                                                                FK see NOTE 1
               emi_rcp          X            code identifying a beam as either transmitting [E] or receiving [R]                                            FK
```


---

## [Página 34]

```text
 Table Name        Data Item      Format                                                 Description                                                                Comment
              beam_name        X(8)          designation of the satellite antenna beam                                                                      FK
              grp_id           9(9)          unique identifier of the group                                                                                 PK, FK see NOTE 1
              seq_no           9(4)          sequence number                                                                                                PK
              freq_sym         X             symbol indicating kilohertz [K], megahertz [M] or gigahertz [G]
              freq_assgn       k:9(5).9(3)   assigned frequency
                               m:9(5).9(6)
                               g:9(4).9(9)
              freq_mhz         9(7).9(6)     assigned frequency in MHz
              freq_min         9(7).9(6)     minimum frequency in MHz (assigned frequency - half bandwidth)
              freq_max         9(7).9(6)     maximum frequency in MHz (assigned frequency + half bandwidth)
              bdwdth           9(8).9(2)     assigned frequency band expressed in kHz
              fdg_reg          X(2)          findings: conformity with Radio Regulations; Table 13A of the Preface to BR IFIC (Space Services)
                                             (13A1)
              d_prot_eff       9(8)          the date from which a list of assignments is taken into account according to RR1061-1065 or RR1148-
                                             1154, as appropriate
              wic_no           9(4)          the number of the WIC/IFIC in which the notice was most recently published                                     BR data
              ntc_type         X             code indicating if the notice is of a geostationary satellite [G], non-geostationary satellite [N], specific
                                             earth station [S], typical earth station [T] or radio astronomy station [R]
history                                      Transaction history data                                                                                       BR internal use
              ntc_id           9(9)          unique identifier of the notice                                                                                PK, FK; see NOTE 1
              seq_no           9(4)          sequence number                                                                                                PK
              oper_id          X(8)          unique identifier of the operator/program
              d_hist           9(8).9(6)     date relating to the action performed by the operator or program
              st_cur           X(2)          current status of the transaction
              hist_text        X(60)         description of the action carried out on the notice
srs_ooak                                     Database system information                                                                                    BR internal use
              version_no       9(2)          number current version of the database                                                                         PK
              version_no_sub   9(2)          minor (or sub) version of the database structure
              d_create         9(8)          date of creation of the database
              d_last_export    9(8)          date of the most recent export of a network into the DB
              comment          X(30)         comment
```
