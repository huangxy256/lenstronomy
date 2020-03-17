import numpy as np
import copy

from lenstronomy.ImSim.image_solve import ImageFit
from lenstronomy.ImSim.Numerics.numerics_subframe import NumericsSubFrame
from lenstronomy.Util import util
from lenstronomy.Util import image_util
import lenstronomy.ImSim.de_lens as de_lens

from slitronomy.Optimization.solver_source import SparseSolverSource
from slitronomy.Optimization.solver_source_lens import SparseSolverSourceLens
from slitronomy.Optimization.solver_source_ps import SparseSolverSourcePS
from slitronomy.Lensing.lensing_operator import LensingOperatorInterpol

class ImageSparseFit(ImageFit):
    """
    sparse inversion class, inherits ImageFit
    """

    def __init__(self, data_class, psf_class, lens_model_class=None, source_model_class=None,
                 lens_light_model_class=None, point_source_class=None, extinction_class=None, kwargs_numerics={}, likelihood_mask=None,
                 psf_error_map_bool_list=None, kwargs_sparse_solver={}):
        """

        :param data_class: ImageData() instance
        :param psf_class: PSF() instance
        :param lens_model_class: LensModel() instance
        :param source_model_class: LightModel() instance
        :param lens_light_model_class: LightModel() instance
        :param point_source_class: PointSource() instance
        :param kwargs_numerics: keyword arguments passed to the Numerics module
        :param likelihood_mask: 2d boolean array of pixels to be counted in the likelihood calculation/linear optimization
        :param psf_error_map_bool_list: list of boolean of length of point source models. Indicates whether PSF error map
        :param kwargs_sparse_solver: keyword arguments passed to `SparseSolverSource`/`SparseSolverSourceLens`/`SparseSolverSourcePS` module of SLITronomy
        being applied to the point sources.
        """
        super(ImageSparseFit, self).__init__(data_class, psf_class, lens_model_class=lens_model_class,
                                             source_model_class=source_model_class,
                                             lens_light_model_class=lens_light_model_class,
                                             point_source_class=point_source_class, extinction_class=extinction_class, 
                                             kwargs_numerics=kwargs_numerics, likelihood_mask=likelihood_mask,
                                             psf_error_map_bool_list=psf_error_map_bool_list)

        # numerics for source plane has a different supersampling resolution
        self._subgrid_res_source = kwargs_sparse_solver.get('subgrid_res_source', 1)
        kwargs_numerics_source = kwargs_numerics.copy()
        kwargs_numerics_source['supersampling_factor'] = self._subgrid_res_source
        self.ImageNumerics_source = NumericsSubFrame(pixel_grid=self.Data, psf=self.PSF, **kwargs_numerics_source)

        # TODO : implement support for numba convolution
        # current implementation of lenstronomy does not allow access to the convolution_class through self.ImageNumerics

        no_lens_light = (self.LensLightModel is None or len(self.LensLightModel.profile_type_list) == 0)
        no_point_sources = (self.PointSource is None or len(self.PointSource.point_source_type_list) == 0)
        if no_lens_light and no_point_sources:
            model_list = self.SourceModel.profile_type_list
            if len(model_list) != 1 or model_list[0] not in ['STARLETS', 'STARLETS_GEN2']:
                raise ValueError("'STARLETS' or 'STARLETS_GEN2' must be the only source model list for sparse fit")
            self.sparseSolver = SparseSolverSource(self.Data, self.LensModel, self.SourceModel, self.ImageNumerics, 
                                                   likelihood_mask=likelihood_mask, 
                                                   **kwargs_sparse_solver)
        elif no_point_sources:
            model_list = self.LensLightModel.profile_type_list
            if len(model_list) != 1 or model_list[0] not in ['STARLETS', 'STARLETS_GEN2']:
                raise ValueError("'STARLETS' or 'STARLETS_GEN2' must be the only lens light model list for sparse fit")
            self.sparseSolver = SparseSolverSourceLens(self.Data, self.LensModel, self.SourceModel, self.LensLightModel, self.ImageNumerics, 
                                                       likelihood_mask=likelihood_mask, 
                                                       **kwargs_sparse_solver)
        elif no_lens_light:
            if not np.all(self.PSF.psf_error_map == 0):
                print("WARNING : SparseSolver with point sources does not support PSF error map for now !")
            self.sparseSolver = SparseSolverSourcePS(self.Data, self.LensModel, self.SourceModel, self.ImageNumerics, 
                                                     self._image_linear_solve_point_sources, #TODO: not fully satisfying
                                                     likelihood_mask=likelihood_mask, 
                                                     **kwargs_sparse_solver)
        # source <-> image pixelated mapping
        self.lensingOperator = LensingOperatorInterpol(self.Data, self.LensModel, subgrid_res_source=self._subgrid_res_source)

    def source_surface_brightness(self, kwargs_source, kwargs_lens=None, kwargs_extinction=None, kwargs_special=None,
                                  unconvolved=False, de_lensed=False, k=None, update_lens_mapping=True):
        """
        Overwrites ImageModel method.
        ImageModel.source_surface_brightness() may not work for some settings.

        computes the source surface brightness distribution

        :param kwargs_source: list of keyword arguments corresponding to the superposition of different source light profiles
        :param kwargs_lens: list of keyword arguments corresponding to the superposition of different lens profiles
        :param kwargs_extinction: list of keyword arguments of extinction model
        :param unconvolved: if True: returns the unconvolved light distribution (prefect seeing)
        :param de_lensed: if True: returns the un-lensed source surface brightness profile, otherwise the lensed.
        :param k: integer, if set, will only return the model of the specific index
        :param update_lens_mapping: if False, prevent 
        :return: 1d array of surface brightness pixels
        """
        # ra_grid, dec_grid = self.lensingOperator.sourcePlane.grid()
        ra_grid, dec_grid = self.ImageNumerics_source.coordinates_evaluate

        # TODO : support source grid offsets (using 'delta_x_source_grid' in kwargs_special)
        # i.e. interpolate the image back to center coordinates

        source_light = self.SourceModel.surface_brightness(ra_grid, dec_grid, kwargs_source, k=k)

        if de_lensed is True:
            source_light = self.ImageNumerics_source.re_size_convolve(source_light, unconvolved=unconvolved)
        else:
            source_light = self.lensingOperator.source2image(source_light, kwargs_lens=kwargs_lens, kwargs_special=kwargs_special,
                                                             update_lens=update_lens_mapping)
            source_light = self.ImageNumerics.re_size_convolve(source_light, unconvolved=unconvolved)
        
        # re_size_convolve multiplied by self.Data.pixel_width**2, but flux normalization is handled in lensingOperator
        source_light_final = source_light / self.Data.pixel_width**2
        return source_light_final

    def lens_surface_brightness(self, kwargs_lens_light, unconvolved=False, k=None):
        """
        computes the lens surface brightness distribution

        If 'unconvolved' is True, a warning message will appear, and the convolved light is returned.
        This is because the sparse optimizer does not solve for the unconvolved lens light, in order to prevent deconvolutions
        that can otherwise reduce the quality of fit. Hence the deconvolution of the lens light should be performed in post-processing.

        :param kwargs_lens_light: list of keyword arguments corresponding to different lens light surface brightness profiles
        :param unconvolved: not defined here. Here for keeping same method signatures as in super class.
        :return: 1d array of surface brightness pixels
        """
        if unconvolved is True:
            print("WARNING : sparse solver for lens light does not perform deconvolution of lens light, returning convolved estimate instead")
        # ra_grid, dec_grid = self.sparseSolver.lensingOperator.imagePlane.grid()
        ra_grid, dec_grid = self.ImageNumerics.coordinates_evaluate
        lens_light = self.LensLightModel.surface_brightness(ra_grid, dec_grid, kwargs_lens_light, k=k)
        lens_light_final = util.array2image(lens_light)
        return lens_light_final

    def image_sparse_solve(self, kwargs_lens=None, kwargs_source=None, kwargs_lens_light=None,
                           kwargs_ps=None, kwargs_extinction=None, kwargs_special=None):
        """
        computes the image (lens and source surface brightness with a given lens model)
        using sparse optimization, on the data pixelated grid.

        :param kwargs_lens: list of keyword arguments corresponding to the superposition of different lens profiles
        :param kwargs_source: list of keyword arguments corresponding to the superposition of different source light profiles
        :param kwargs_lens_light: list of keyword arguments corresponding to different lens light surface brightness profiles
        :param kwargs_ps: keyword arguments corresponding to point sources
        :param kwargs_extinction: keyword arguments corresponding to dust extinction
        :param kwargs_special: keyword arguments corresponding to "special" parameters
        :return: 1d array of surface brightness pixels of the optimal solution of the linear parameters to match the data
        """
        #TODO: add the 'inv_bool' parameters like in super.image_linear_solve for point source linear inversion ?
        return self._image_sparse_solve(kwargs_lens, kwargs_source, kwargs_lens_light, 
                                        kwargs_ps, kwargs_extinction, kwargs_special)

    def _image_sparse_solve(self, kwargs_lens=None, kwargs_source=None, kwargs_lens_light=None, 
                            kwargs_ps=None, kwargs_extinction=None, kwargs_special=None):
        """
        computes the image (lens and source surface brightness with a given lens model)
        using sparse optimization, on the data pixelated grid.

        :param kwargs_lens: list of keyword arguments corresponding to the superposition of different lens profiles
        :param kwargs_source: list of keyword arguments corresponding to the superposition of different source light profiles
        :param kwargs_lens_light: list of keyword arguments corresponding to different lens light surface brightness profiles
        :param kwargs_ps: keyword arguments corresponding to point sources
        :param kwargs_extinction: keyword arguments corresponding to dust extinction
        :param kwargs_special: keyword arguments corresponding to "special" parameters
        :return: 1d array of surface brightness pixels of the optimal solution of the linear parameters to match the data
        """
        C_D_response, model_error = self._error_response(kwargs_lens, kwargs_ps, kwargs_special=kwargs_special)
        init_ps_model = self.point_source(kwargs_ps, kwargs_lens=kwargs_lens, kwargs_special=kwargs_special)
        model, param = self.sparseSolver.solve(kwargs_lens, kwargs_source, kwargs_lens_light=kwargs_lens_light,
                                               kwargs_ps=kwargs_ps, kwargs_special=kwargs_special,
                                               init_ps_model=init_ps_model)
        _, _, _, _ = self.update_sparse_kwargs(param, kwargs_lens, kwargs_source, kwargs_lens_light, kwargs_ps)
        return model, model_error, param

    def likelihood_data_given_model(self, kwargs_lens=None, kwargs_source=None, kwargs_lens_light=None, kwargs_ps=None,
                                    kwargs_special=None):
        """
        computes the likelihood of the data given a model
        This is specified with the non-linear parameters and a linear inversion and prior marginalisation.

        :param kwargs_lens: list of keyword arguments corresponding to the superposition of different lens profiles
        :param kwargs_source: list of keyword arguments corresponding to the superposition of different source light profiles
        :param kwargs_lens_light: list of keyword arguments corresponding to different lens light surface brightness profiles
        :param kwargs_ps: keyword arguments corresponding to point sources
        :param kwargs_extinction: keyword arguments corresponding to dust extinction
        :param kwargs_special: keyword arguments corresponding to "special" parameters        :return: log likelihood (natural logarithm)
        """
        return self._likelihood_data_given_model(kwargs_lens, kwargs_source, kwargs_lens_light, kwargs_ps,
                                                 kwargs_special)

    def _likelihood_data_given_model(self, kwargs_lens=None, kwargs_source=None, kwargs_lens_light=None, kwargs_ps=None,
                                     kwargs_special=None):
        """

        computes the likelihood of the data given a model
        This is specified with the non-linear parameters and a linear inversion and prior marginalisation.

        :param kwargs_lens: list of keyword arguments corresponding to the superposition of different lens profiles
        :param kwargs_source: list of keyword arguments corresponding to the superposition of different source light profiles
        :param kwargs_lens_light: list of keyword arguments corresponding to different lens light surface brightness profiles
        :param kwargs_ps: keyword arguments corresponding to point sources
        :param kwargs_extinction: keyword arguments corresponding to dust extinction
        :param kwargs_special: keyword arguments corresponding to "special" parameters        :param source_marg: bool, performs a marginalization over the linear parameters
        :return: log likelihood (natural logarithm)
        """
        # generate image
        im_sim, model_error, param = self._image_sparse_solve(kwargs_lens=kwargs_lens, kwargs_source=kwargs_source, 
                                                              kwargs_lens_light=kwargs_lens_light, kwargs_special=kwargs_special)
        # compute X^2
        logL = self.Data.log_likelihood(im_sim, self.likelihood_mask, model_error)
        if not np.isfinite(logL):
            return -1e20  # penalty
        return logL

    def _image_linear_solve_point_sources(self, sparse_model, kwargs_lens=None, kwargs_ps=None, kwargs_special=None, inv_bool=False):
        """
        linear solve, but only for point sources. The target image is the imaging data with sparse model subtracted (source and lens light)

        computes the image (point source amplitudes with a given lens model).
        The linear parameters are computed with a weighted linear least square optimization (i.e. flux normalization of the brightness profiles)

        :param kwargs_lens: list of keyword arguments corresponding to the superposition of different lens profiles
        :param kwargs_source: list of keyword arguments corresponding to the superposition of different source light profiles
        :param kwargs_lens_light: list of keyword arguments corresponding to different lens light surface brightness profiles
        :param kwargs_ps: keyword arguments corresponding to "other" parameters, such as external shear and point source image positions
        :param inv_bool: if True, invert the full linear solver Matrix Ax = y for the purpose of the covariance matrix.
        :return: 1d array of surface brightness pixels of the optimal solution of the linear parameters to match the data
        """
        A = self.point_source_linear_response_matrix(kwargs_lens, kwargs_ps, kwargs_special)
        C_D_response, model_error = self._error_response(kwargs_lens, kwargs_ps, kwargs_special=kwargs_special)
        d = self.data_response - self.image2array_masked(sparse_model)  # subract source light + lens light model
        param, cov_param, wls_model = de_lens.get_param_WLS(A.T, 1 / C_D_response, d, inv_bool=inv_bool)
        _, _ = self.update_linear_kwargs_point_source(param, kwargs_lens, kwargs_ps)
        model = self.array_masked2image(wls_model)
        return model, model_error, cov_param, param

    def point_source_linear_response_matrix(self, kwargs_lens, kwargs_ps, kwargs_special):
        """

        return linear response Matrix, with only point sources.

        :param kwargs_lens:
        :param kwargs_source:
        :param kwargs_lens_light:
        :param kwargs_ps:
        :param unconvolved:
        :return:
        """
        ra_pos, dec_pos, amp, num_param = self.point_source_linear_response_set(kwargs_ps, kwargs_lens, kwargs_special, with_amp=False)
        num_response = self.num_data_evaluate
        A = np.zeros((num_param, num_response))
        # response of point sources
        for i in range(0, num_param):
            image = self.ImageNumerics.point_source_rendering(ra_pos[i], dec_pos[i], amp[i])
            A[i, :] = self.image2array_masked(image)
        return np.nan_to_num(A)
        
    def update_linear_kwargs_point_source(self, param, kwargs_lens, kwargs_ps):
        """

        links linear parameters to kwargs arguments, with only point sources.

        :param param: linear parameter vector corresponding to the response matrix
        :return: updated list of kwargs with linear parameter values
        """
        i = 0
        kwargs_ps, i = self.PointSource.update_linear(param, i, kwargs_ps, kwargs_lens)
        return kwargs_lens, kwargs_ps

    def update_sparse_kwargs(self, param, kwargs_lens, kwargs_source, kwargs_lens_light, kwargs_ps):
        """

        links linear parameters to kwargs arguments

        :param param: linear parameter vector corresponding to the response matrix
        :return: updated list of kwargs with linear parameter values
        """
        kwargs_source, kwargs_lens_light = self.update_fixed_param(kwargs_source, kwargs_lens_light)
        kwargs_lens, kwargs_source, kwargs_lens_light, kwargs_ps = self.update_linear_kwargs(param, kwargs_lens, kwargs_source, kwargs_lens_light, kwargs_ps)
        return kwargs_lens, kwargs_source, kwargs_lens_light, kwargs_ps

    def update_fixed_param(self, kwargs_source, kwargs_lens_light):
        # in case the source plane grid size has changed, update the kwargs accordingly
        kwargs_source[0]['n_pixels'] = int(self.Data.num_pixel * self._subgrid_res_source**2)  # effective number of pixels in source plane
        kwargs_source[0]['scale'] = self.Data.pixel_width / self._subgrid_res_source        # effective pixel size of source plane grid
        # pixelated reconstructions have no well-defined center, we put it arbitrarily at (0, 0), center of the image
        kwargs_source[0]['center_x'] = 0
        kwargs_source[0]['center_y'] = 0
        # do the same if the lens light has been reconstructed
        if kwargs_lens_light is not None and len(kwargs_lens_light) > 0:
            kwargs_lens_light[0]['n_pixels'] = self.Data.num_pixel
            kwargs_lens_light[0]['scale'] = self.Data.pixel_width
            kwargs_lens_light[0]['center_x'] = 0
            kwargs_lens_light[0]['center_y'] = 0
        return kwargs_source, kwargs_lens_light
