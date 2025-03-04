import copy
import numpy as np
import numpy.testing as npt
from lenstronomy.LightModel.Profiles.shapelets_ellipse import ShapeletSetEllipse
from lenstronomy.LightModel.Profiles.shapelets import ShapeletSet
from lenstronomy.Util import param_util, util


class TestShapeletSetEllipse(object):
    def setup_method(self, method):
        self.ellipse = ShapeletSetEllipse()
        self.spherical = ShapeletSet()

    def test_function(self):
        x, y = util.make_grid(numPix=10, deltapix=1)

        e1, e2 = 0.1, -0.2
        center_x, center_y = 1, -1
        n_max = 3
        num_param = int((n_max + 1) * (n_max + 2) / 2)
        amp_list = np.ones(num_param)
        kwargs_spherical = {
            "amp": amp_list,
            "beta": 1,
            "n_max": n_max,
            "center_x": center_x,
            "center_y": center_y,
        }
        kwargs_ellipse = copy.deepcopy(kwargs_spherical)
        kwargs_ellipse["e1"] = e1
        kwargs_ellipse["e2"] = e2

        x_, y_ = param_util.transform_e1e2_product_average(
            x, y, e1, e2, center_x=center_x, center_y=center_y
        )
        x_ += center_x
        y_ += center_y

        flux_ellipse = self.ellipse.function(x, y, **kwargs_ellipse)
        flux_spherical = self.spherical.function(x_, y_, **kwargs_spherical)
        npt.assert_almost_equal(flux_ellipse, flux_spherical, decimal=8)

    def test_function_split(self):
        x, y = util.make_grid(numPix=10, deltapix=1)

        e1, e2 = 0.1, -0.2
        center_x, center_y = 1, -1
        n_max = 3
        num_param = int((n_max + 1) * (n_max + 2) / 2)
        amp_list = np.ones(num_param)
        kwargs_spherical = {
            "amp": amp_list,
            "beta": 1,
            "n_max": n_max,
            "center_x": center_x,
            "center_y": center_y,
        }
        kwargs_ellipse = copy.deepcopy(kwargs_spherical)
        kwargs_ellipse["e1"] = e1
        kwargs_ellipse["e2"] = e2

        x_, y_ = param_util.transform_e1e2_product_average(
            x, y, e1, e2, center_x=center_x, center_y=center_y
        )
        x_ += center_x
        y_ += center_y

        flux_ellipse = self.ellipse.function_split(x, y, **kwargs_ellipse)
        flux_spherical = self.spherical.function_split(x_, y_, **kwargs_spherical)
        npt.assert_almost_equal(flux_ellipse, flux_spherical, decimal=8)
