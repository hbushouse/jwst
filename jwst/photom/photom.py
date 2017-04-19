#  Module for extraction of photom conversion factor(s)
#        and writing them to input header
#

import logging
import numpy as np
from .. import datamodels
from .. datamodels import dqflags

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

PHOT_TOL = 0.001  # relative tolerance between PIXAR_* keys

class DataSet(object):
    """
    Input dataset to which the photom information will be attached

    Parameters
    ----------

    """
    def __init__(self, model, phot_file=None, area_file=None):
        """
        Short Summary
        -------------
        Set file name of input file, photom ref file, area ref file,
        instrument, detector, filter, pupil, and exposure type.

        Parameters
        ----------
        model: data model object
            input Data Model object

        phot_file: string
           name of photom reference file

        area_file: string
           name of pixel area reference file

        """
        self.input = model
        self.phot_file = phot_file
        self.area_file = area_file

        self.instrument = model.meta.instrument.name.upper()
        self.detector = model.meta.instrument.detector.upper()
        self.exptype = model.meta.exposure.type.upper()
        self.filter = None
        if model.meta.instrument.filter is not None:
            self.filter = model.meta.instrument.filter.upper()
        self.pupil = None
        if model.meta.instrument.pupil is not None:
            self.pupil = model.meta.instrument.pupil.upper()
        self.grating = None
        if model.meta.instrument.grating is not None:
            self.grating = model.meta.instrument.grating.upper()
        self.band = None
        if model.meta.instrument.band is not None:
            self.band = model.meta.instrument.band.upper()
        self.slitnum = -1

        log.info('Using instrument: %s', self.instrument)
        log.info(' detector: %s', self.detector)
        log.info(' exp_type: %s', self.exptype)
        if self.filter is not None:
            log.info(' filter: %s', self.filter)
        if self.pupil is not None:
            log.info(' pupil: %s', self.pupil)
        if self.grating is not None:
            log.info(' grating: %s', self.grating)
        if self.band is not None:
            log.info(' band: %s', self.band)


    def calc_nirspec(self, ftab):
        """
        Extended Summary
        -------------
        For the NIRSPEC instrument, extract PHOTMJSR from the input model to
        write to the output model. Matching is based on FILTER and GRATING.

        The routine will find the corresponding information in the reference
        file and write the conversion factor to the output model.  The routine
        will also check to see if there is a wavelength-dependent response
        table; if there is it will be written to the output model.

        Parameters
        ----------
        ftab: fits HDUList
            HDUList for NIRSPEC reference file

        Returns
        -------
        conv_factor: float
            photometric conversion factor written as PHOTMJSR

        """
        conv_factor = None

        # Get the GRATING value from the input data model
        grating = self.input.meta.instrument.grating.upper()

        # Handle fixed-slit exposures separately
        if (isinstance(self.input, datamodels.MultiSlitModel) and
            self.exptype in ['NRS_FIXEDSLIT', 'NRS_BRIGHTOBJ']):

            # We have to find and attach a separate set of flux cal
            # data for each of the fixed slits in the input
            for slit in self.input.slits:

                log.info('Working on slit %s' % slit.name)
                conv_factor = None
                self.slitnum += 1

                # Loop through reference table to find matching row
                for tabdata in ftab.phot_table:
                    ref_filter = tabdata['filter'].strip().upper()
                    ref_grating = tabdata['grating'].strip().upper()
                    ref_slit = tabdata['slit'].strip().upper()
                    log.debug(' Ref table data: %s %s %s' %
                              (ref_filter, ref_grating, ref_slit))

                    # Match on filter, grating, and slit name
                    if (self.filter == ref_filter and
                        grating == ref_grating and
                        slit.name == ref_slit):
                        conv_factor = self.photom_io(tabdata)
                        break

                if conv_factor is None:
                    log.warning('Did not find a match in the ref file')

            if conv_factor is not None:
                return float(conv_factor)
            else:
                return 0.0

        # IFU and MSA exposures use one set of flux cal data
        else:

            # Loop through the reference table to find matching row
            for tabdata in ftab.phot_table:

                ref_filter = tabdata['filter'].strip().upper()
                ref_grating = tabdata['grating'].strip().upper()

                # Match on filter and grating only
                if self.filter == ref_filter and grating == ref_grating:

                    # MSA data
                    if (isinstance(self.input, datamodels.MultiSlitModel) and
                        self.exptype == 'NRS_MSASPEC'):

                        # Loop over the MSA slits, applying the same photom
                        # ref data to all slits
                        for slit in self.input.slits:
                            log.info('Working on slit %s' % slit.name)
                            self.slitnum += 1
                            conv_factor = self.photom_io(tabdata)

                    # IFU data
                    else:

                        # Get the conversion factor from the PHOTMJSR column
                        conv_factor = tabdata['photmjsr']

                        # Get the length of the relative response arrays in this row
                        nelem = tabdata['nelem']

                        # If the relative response arrays have length > 0, copy them into the
                        # relsens table of the data model
                        if nelem > 0:
                            waves = tabdata['wavelength'][:nelem]
                            relresps = tabdata['relresponse'][:nelem]

                            # Convert wavelengths from meters to microns, if necessary
                            MICRONS_100 = 1.e-4         # 100 microns, in meters
                            if waves.max() > 0. and waves.max() < MICRONS_100:
                                waves *= 1.e+6

                        # Load the pixel area table for the IFU slices
                        area_model = datamodels.NirspecAreaModel(self.area_file)
                        area_data = area_model.ifu_table

                        # Compute 2D wavelength and pixel area arrays for the whole image
                        wave2d, area2d, dqmap = self.calc_nrs_ifu_sens2d(area_data)

                        # Compute relative sensitivity for each pixel based on wavelength
                        sens2d = np.interp(wave2d, waves, relresps)
                        #datamodels.ImageModel(data=sens2d).save('phot_sens2d1.fits')

                        # Include the scalar conversion factor
                        sens2d *= conv_factor

                        # Divide by pixel area
                        sens2d /= area2d

                        # Reset NON_SCIENCE pixels to 1 in sens2d array and in
                        # science data dq array
                        where_dq = np.bitwise_and(dqmap, dqflags.pixel['NON_SCIENCE'])
                        sens2d[where_dq > 0] = 1.
                        self.input.dq = np.bitwise_or(self.input.dq, dqmap)

                        # FOR DEBUGGING ONLY #
                        #datamodels.ImageModel(data=wave2d).save('phot_wave2d.fits')
                        #datamodels.ImageModel(data=area2d).save('phot_area2d.fits')
                        #datamodels.ImageModel(data=sens2d).save('phot_sens2d2.fits')

                        # Divide the science data and err by the conversion factors
                        self.input.data /= sens2d
                        self.input.err /= sens2d

                        # Store the 2D sensitivity factors
                        self.input.relsens2d = sens2d

                        # Update BUNIT values for the science data and err
                        self.input.meta.bunit_data = 'mJy/arcsec^2'
                        self.input.meta.bunit_err = 'mJy/arcsec^2'

                        area_model.close()

                    break

            if conv_factor is not None:
                return float(conv_factor)
            else:
                log.warning('Did not find a match in the ref file, so returning'
                            ' conversion factor 0.0')
                return 0.0


    def calc_niriss(self, ftab):
        """
        Extended Summary
        -------------
        For the NIRISS instrument, extract PHOTMJSR from the input model to
        write to the output model. Matching is based on FILTER and PUPIL.
        There may be multiple entries for a given FILTER+PUPIL combination,
        corresponding to different spectral orders. Data for all orders will
        be retrieved.

        The routine will find the corresponding information in the reference
        file and write the conversion factor to the output model.  The routine
        will also check if there is a wavelength-dependent response table; if
        there is it will be written to the output model.

        Parameters
        ----------
        ftab: fits HDUList
            HDUList for NIRISS reference file

        Returns
        -------
        conv_factor: float
            photometric conversion factor written as PHOTMJSR
        """
        conv_factor = None

        # Handle MultiSlit models separately
        if isinstance(self.input, datamodels.MultiSlitModel):

            # We have to find and attach a separate set of flux cal
            # data for each of the slits/orders in the input
            for slit in self.input.slits:

                log.info('Working on slit %s' % slit.name)
                conv_factor = None
                self.slitnum += 1

                # Set the input data order number.
                # This is a hack for now; eventually the order number
                # should be specified in the input data model
                order = self.slitnum + 1

                # Locate matching row in reference file
                for tabdata in ftab.phot_table:

                    ref_filter = tabdata['filter'].strip().upper()
                    ref_pupil = tabdata['pupil'].strip().upper()
                    ref_order = tabdata['order']

                    # Find matching values of FILTER, PUPIL, ORDER
                    if (self.filter == ref_filter and
                        self.pupil == ref_pupil and
                        order == ref_order):
                        conv_factor = self.photom_io(tabdata)
                        break

                if conv_factor is None:
                    log.warning('Did not find a match in the ref file')

            if conv_factor is not None:
                return float(conv_factor)
            else:
                return 0.0

        # Simple ImageModels
        else:

            # Hardwire the science data order number to 1 for now
            order = 1

            # Locate matching row in reference file
            for tabdata in ftab.phot_table:
                ref_filter = tabdata['filter'].strip().upper()
                ref_pupil = tabdata['pupil'].strip().upper()
                ref_order = tabdata['order']

                # Spectroscopic mode
                if self.exptype in ['NIS_SOSS', 'NIS_WFSS']:

                    # Find matching values of FILTER, PUPIL, and ORDER
                    if (self.filter == ref_filter and self.pupil == ref_pupil
                        and order == ref_order):
                        conv_factor = self.photom_io(tabdata)
                        break

                # Imaging mode
                else:

                    # Find matching values of FILTER and PUPIL
                    if (self.filter == ref_filter and self.pupil == ref_pupil):
                        conv_factor = self.photom_io(tabdata)
                        break

            if conv_factor is not None:
                return float(conv_factor)
            else:
                log.warning('Did not find a match in the ref file, so returning'
                            ' conversion factor 0.0')
                return 0.0


    def calc_miri(self, ftab):
        """
        Extended Summary
        -------------
        For the MIRI instrument, extract PHOTMJSR from the input model to
        write to the output model.

        For the imaging detector, matching is based on FILTER and SUBARRAY.

        For the MRS detectors, matching is based on BAND.

        The routine will find the corresponding information in the reference
        file and write the conversion factor to the output model.  The routine
        will also check if there is a wavelength-dependent response table; if
        there is it will be written to the output model.

        Parameters
        ----------
        ftab: fits HDUList
            HDUList for MIRI reference file

        Returns
        -------
        conv_factor: float
            photometric conversion factor written as PHOTMJSR
        """
        conv_factor = None

        # Imaging detector
        if self.detector == 'MIRIMAGE':

            # Get the subarray value of the input data model
            subarray = self.input.meta.subarray.name
            log.debug(' subarray: %s', subarray)

            # Find the matching row in the reference file
            for tabdata in ftab.phot_table:

                ref_filter = tabdata['filter'].strip().upper()
                ref_subarray = tabdata['subarray'].strip().upper()

                # If the ref file subarray entry is GENERIC, it's an automatic
                # match to the science data subarray value
                if ref_subarray == 'GENERIC':
                    ref_subarray = subarray

                # Find matching FILTER and SUBARRAY values
                if self.filter == ref_filter and subarray == ref_subarray:
                    conv_factor = self.photom_io(tabdata)
                    break

        # MRS detectors
        elif self.detector == 'MIRIFUSHORT' or self.detector == 'MIRIFULONG':

            # Reset conversion and pixel size values with DQ=NON_SCIENCE to 1,
            # so no conversion is applied
            where_dq = np.bitwise_and(ftab.dq, dqflags.pixel['NON_SCIENCE'])
            ftab.data[where_dq > 0] = 1.0
            ftab.pixsiz[where_dq > 0] = 1.0

            # Reset NaN's in conversion array to 1
            where_nan = np.isnan(ftab.data)
            ftab.data[where_nan] = 1.0

            # Reset zeros in pixsiz array to 1
            where_zero = np.where(ftab.pixsiz == 0.0)
            ftab.pixsiz[where_zero] = 1.0

            # Make sure all NaN's and zeros have DQ flags set
            ftab.dq[where_nan] = np.bitwise_or(ftab.dq[where_nan],
                                               dqflags.pixel['NON_SCIENCE'])
            ftab.dq[where_zero] = np.bitwise_or(ftab.dq[where_zero],
                                                dqflags.pixel['NON_SCIENCE'])

            # Divide the science data and err by the conversion factors
            self.input.data /= ftab.data * ftab.pixsiz
            self.input.err /= ftab.data * ftab.pixsiz

            # Update the science dq
            self.input.dq = np.bitwise_or(self.input.dq, ftab.dq)

            # Store the 2D sensitivity factors
            self.input.relsens2d = ftab.data * ftab.pixsiz

            # Retrieve the scalar conversion factor from the reference data
            conv_factor = ftab.meta.photometry.conversion_megajanskys

            # Update BUNIT values for the science data and err
            self.input.meta.bunit_data = 'mJy/arcsec^2'
            self.input.meta.bunit_err = 'mJy/arcsec^2'

        if conv_factor is not None:
            return float(conv_factor)
        else:
            log.warning('Did not find a match in the ref file, so returning'
                        ' conversion factor 0.0')
            return 0.0


    def calc_nircam(self, ftab):
        """
        Extended Summary
        -------------
        For the NIRCAM instrument, extract PHOTMJSR from the input model to
        write to the output model. Matching is based on FILTER and PUPIL.

        The routine will find the corresponding information in the reference
        file and write the conversion factor to the output model.  The routine
        will also check if there is a wavelength-dependent response table; if
        there is it will be written to the output model.

        Parameters
        ----------
        ftab: fits HDUList
            HDUList for NIRCAM reference file

        Returns
        -------
        conv_factor: float
            photometric conversion factor written as PHOTMJSR
        """
        conv_factor = None

        # Locate relevant information in reference file
        for tabdata in ftab.phot_table:

            ref_filter = tabdata['filter'].strip().upper()
            ref_pupil = tabdata['pupil'].strip().upper()

            # Finding matching FILTER and PUPIL values
            if self.filter == ref_filter and self.pupil == ref_pupil:
                conv_factor = self.photom_io(tabdata)
                break

        if conv_factor is not None:
            return float(conv_factor)
        else:
            log.warning('Did not find a match in the ref file, so returning'
                        ' conversion factor 0.0')
            return 0.0


    def calc_fgs(self, ftab):
        """
        Extended Summary
        -------------
        For the FGS instrument, extract PHOTMJSR from the input model to
        write to the output model. There is no FILTER or PUPIL wheel, so the
        only mode is CLEAR.

        The routine will find the corresponding information in the reference
        file (which should have only a single row) and write the conversion
        factor to the output model.  The routine will also check if there is
        a wavelength-dependent response table; if there is it will be written
        to the output model.

        Parameters
        ----------
        ftab: fits HDUList
            HDUList for FGS reference file

        Returns
        -------
        conv_factor: float
            photometric conversion factor written as PHOTMJSR
        """
        conv_factor = None

        # Read the first (and only) row in the reference file
        for tabdata in ftab.phot_table:
            conv_factor = self.photom_io(tabdata)
            break

        if conv_factor is not None:
            return float(conv_factor)
        else:
            log.warning('Did not find a match in the ref file, so returning'
                        ' conversion factor 0.0')
            return 0.0


    def calc_nrs_ifu_sens2d(self, area_data):

        import math
        import numpy as np
        from .. assign_wcs import nirspec       # for NIRSpec IFU data

        MICRONS_100 = 1.e-4                     # 100 microns, in meters

        # Create empty 2D arrays for the wavelengths and pixel areas
        wave2d = np.zeros_like(self.input.data)
        area2d = np.ones_like(self.input.data)

        # Create and initialize an array for the 2D dq map to be returned.
        # initialize all pixels to NON_SCIENCE, because operations below
        # only touch pixels within the bounding_box of each slice
        dqmap = np.zeros_like(self.input.dq) + dqflags.pixel['NON_SCIENCE']

        # Get the list of wcs's for the IFU slices
        list_of_wcs = nirspec.nrs_ifu_wcs(self.input)

        # Loop over the slices
        for (k, ifu_wcs) in enumerate(list_of_wcs):

            # Get the corners for the slice
            xstart = ifu_wcs.bounding_box[0][0]
            xstop = ifu_wcs.bounding_box[0][1]
            ystart = ifu_wcs.bounding_box[1][0]
            ystop = ifu_wcs.bounding_box[1][1]

            log.debug("Slice %d: %g %g %g %g" % (k, xstart, xstop, ystart, ystop))

            # Check for corner values off the edge
            if xstart < -0.5:
                log.debug("xstart from WCS bounding_box was %g" % xstart)
                xstart = 0.
            if ystart < -0.5:
                log.debug("ystart from WCS bounding_box was %g" % ystart)
                ystart = 0.
            if xstop > 2047.5:
                log.debug("xstop from WCS bounding_box was %g" % xstop)
                xstop = 2047.
            if ystop > 2047.5:
                log.debug("ystop from WCS bounding_box was %g" % ystop)
                ystop = 2047.

            # Convert these to integers, and add one to the upper limits,
            # because we want to use these as slice limits.
            xstart = int(math.ceil(xstart))
            xstop = int(math.floor(xstop)) + 1
            ystart = int(math.ceil(ystart))
            ystop = int(math.floor(ystop)) + 1

            # Construct array indexes for pixels in this slice
            dx = xstop - xstart
            dy = ystop - ystart
            ind = np.indices((dy, dx))
            x = ind[1] + xstart
            y = ind[0] + ystart

            # Get the world coords for all pixels in this slice
            coords = ifu_wcs(x, y)

            # Pull out the wavelengths only
            wl = coords[2]
            nan_flag = np.isnan(wl)
            good_flag = np.logical_not(nan_flag)
            if wl[good_flag].max() < MICRONS_100:
                log.info("Wavelengths in WCS table appear to be in meters")

            # Set NaNs to a harmless value, but don't modify nan_flag.
            wl[nan_flag] = 0.

            # Mark pixels with no wavelength as non-science
            dq = np.zeros_like(wl)
            dq[nan_flag] = dqflags.pixel['NON_SCIENCE']
            dqmap[ystart:ystop, xstart:xstop] = dq

            # Insert the wavelength values for this slice into the
            # whole image array
            wave2d[ystart:ystop, xstart:xstop] = wl

            # Insert the pixel area value for this slice into the
            # whole image array
            ar = np.ones_like(wl)
            ar[:,:] = \
                area_data[np.where(area_data['slice_id']==k)]['pixarea'][0]
            ar[nan_flag] = 1.
            area2d[ystart:ystop, xstart:xstop] = ar

        return wave2d, area2d, dqmap


    def photom_io(self, tabdata):
        """
        Short Summary
        -------------
        Read the conversion photom factor, and attach the relative sensitivity
        table if the number of rows in the table > 0.

        Parameters
        ----------
        tabdata: FITS record
            Single row of data from reference table

        Returns
        -------
        conv_factor: float
            photometric conversion factor

        """
        # Get the conversion factor from the PHOTMJSR column of the table row
        conv_factor = tabdata['photmjsr']

        # Get the length of the relative response arrays in this row
        nelem = tabdata['nelem']

        # If the relative response arrays have length > 0, copy them into the
        # relsens table of the data model
        if nelem > 0:
            waves = tabdata['wavelength'][:nelem]
            relresps = tabdata['relresponse'][:nelem]

            # Convert wavelengths from meters to microns, if necessary
            MICRONS_100 = 1.e-4         # 100 microns, in meters
            if waves.max() > 0. and waves.max() < MICRONS_100:
                waves *= 1.e+6
            wl_unit = 'microns'

            # Set the relative sensitivity table for the correct Model type
            if isinstance(self.input, datamodels.MultiSlitModel):
                otab = np.array(list(zip(waves, relresps)),
                       dtype=self.input.slits[self.slitnum].relsens.dtype)
                self.input.slits[self.slitnum].relsens = otab

            else:
                otab = np.array(list(zip(waves, relresps)),
                                dtype=self.input.relsens.dtype)
                self.input.relsens = otab

            log.info('Relative response table written.')

        return conv_factor


    def save_area_info(self, ftab, area_fname):
        """
        Short Summary
        -------------
        Read the pixel area values in the PIXAR_A2 and PIXAR_SR keys from the
        meta data in the table reference file and in the pixel area reference
        file. If all the keys are present in both reference files, and if the
        the difference in the values of the pixel areas in square arc seconds
        does not exceed the threshold, these 2 keywords are written to the
        primary extension of the output product. Copy the pixel area array from
        the pixel area reference file to the area extension of the output
        product.

        Parameters
        ----------
        ftab: DataModel object
            Instance for photom table reference file

        area_fname: string
            pixel area map file name

        Returns
        -------
        None

        """
        pix_area = datamodels.PixelAreaModel(area_fname)

        # Set model-dependent attribute to area array
        if isinstance(self.input, datamodels.MultiSlitModel):
            self.input.slits[0].area = pix_area.data
        else:
            self.input.area = pix_area.data

        # access pixel areas in steradians and arcsec squared from
        # the table ref file and the pixel area ref file
        # (the *_ster keys are read but not used)
        try:
            tab_ster = ftab.meta.photometry.pixelarea_steradians
            tab_a2 = ftab.meta.photometry.pixelarea_arcsecsq
            area_ster = pix_area.meta.photometry.pixelarea_steradians
            area_a2 = pix_area.meta.photometry.pixelarea_arcsecsq

            a2_tol = abs(tab_a2 - area_a2) / (tab_a2 + area_a2) # rel. tolerance
            if (a2_tol > PHOT_TOL):
                log.warning('The relative difference between the values for the')
                log.warning('pixel area in sq arcsec (%s)', a2_tol)
                log.warning('exceeds the allowed tolerance (%s)', PHOT_TOL)
            else:  # copy the keys to the primary header of the output
                log.debug('The values of the pixel areas (PIXAR_A2 and PIXAR_SR)')
                log.debug('will be copied to the output.')
                self.input.meta.photometry.pixelarea_arcsecsq = float(tab_a2)
                self.input.meta.photometry.pixelarea_steradians = float(tab_ster)
        except: # at least 1 keyword is missing so keys will not be written
            pass

        log.info('Pixel area map written.')

        return None


    def apply_photom(self, photom_fname, area_fname):
        """
        Short Summary
        -------------
        Open the reference file, retrieve the conversion factor, and write that
        factor as the key PHOTMJSR to header of input.  The conversion factor
        for each instrument has its own dependence on detector- and
        observation-specific parameters.  The corresponding conversion factor
        from the reference file is written as PHOTMJSR to the model. The table
        of relative response vs wavelength will be read from the reference file,
        and if it contains >0 rows, it will be attached to the model. If this
        is an imaging mode, there will be a pixel area map file, from which the
        pixel area array will be copied to the area extension of the output
        product. The keywords having the pixel area values are read from the
        map file and compared to their values in the table reference file. If
        these values agree, these keywords will be written to the output.

        Parameters
        ----------
        photom_fname: string
            photom file name of FITS table

        area_fname: string
            pixel area map file name

        Returns
        -------

        """
        if self.instrument == 'NIRISS':
            ftab = datamodels.NirissPhotomModel(photom_fname)
            conv_factor = self.calc_niriss(ftab)

        if self.instrument == 'NIRSPEC':
            if self.exptype in ['NRS_FIXEDSLIT', 'NRS_BRIGHTOBJ']:
                ftab = datamodels.NirspecFSPhotomModel(photom_fname)
            else:
                ftab = datamodels.NirspecPhotomModel(photom_fname)
            conv_factor = self.calc_nirspec(ftab)

        if self.instrument == 'NIRCAM':
            ftab = datamodels.NircamPhotomModel(photom_fname)
            conv_factor = self.calc_nircam(ftab)

        if self.instrument == 'MIRI':
            if self.detector == 'MIRIMAGE':
                ftab = datamodels.MiriImgPhotomModel(photom_fname)
            else:
                ftab = datamodels.MiriMrsPhotomModel(photom_fname)
            conv_factor = self.calc_miri(ftab)

        if self.instrument == 'FGS':
            ftab = datamodels.FgsPhotomModel(photom_fname)
            conv_factor = self.calc_fgs(ftab)

        if conv_factor is None:
            log.warning('Scale factor not found in reference file.')
            conv_factor = 0.0

        if area_fname is not None: # Load and save the pixel area info
            if 'IMAGE' in self.exptype and area_fname != 'N/A':
                result = self.save_area_info(ftab, area_fname)

        # Store the conversion factors in the meta data
        log.info('Writing PHOTMJSR with value: %g', conv_factor)
        self.input.meta.photometry.conversion_megajanskys = conv_factor
        self.input.meta.photometry.conversion_microjanskys = 23.50443 * conv_factor

        ftab.close()


    def do_all(self):
        """
        Short Summary
        -------------
        Execute all tasks for Photom Correction

        Parameters
        ----------

        Returns
        -------
        self.input: DM object
            input DM object with conversion factors attached/applied

        """
        photom_fname = self.phot_file
        area_fname = self.area_file
        self.apply_photom(photom_fname, area_fname)

        return self.input
