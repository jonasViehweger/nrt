import numpy as np
import xarray as xr

from nrt.monitor import BaseNrt
from nrt.utils_efp import _mosum_ols_test_crit, _mosum_init_window


class MoSum(BaseNrt):
    """Monitoring using moving sums (MOSUM) of residuals

    Implementation following method as implemented in R package bFast.

    Attributes:
        mask (numpy.ndarray): A 2D numpy array containing pixels that should
            be monitored (1) and not (0). The mask may be updated following
            history period stability check, and after a call to monitor
            following a confirmed break. Values are as follow.
            ``{0: 'Not monitored', 1: 'monitored', 2: 'Unstable history',
            3: 'Confirmed break - no longer monitored'}``
        trend (bool): Indicate whether stable period fit is performed with
            trend or not
        harmonic_order (int): The harmonic order of the time-series regression
        x (numpy.ndarray): array of x coordinates
        y (numpy.ndarray): array of y coordinates
        sensitivity (float): sensitivity of the monitoring. Lower numbers
            correspond to lower sensitivity. Equivalent to significance level
            'alpha' with which the boundary is computed
        boundary (numpy.ndarray): process boundary for each time series.
            Calculated from alpha and length of time series.
        sigma (numpy.ndarray): Standard deviation for normalized residuals in
            history period
        histsize (numpy.ndarray): Number of non-nan observations in history
            period
        n (numpy.ndarray): Total number of non-nan observations in time-series
        critval (float): Critical test value corresponding to the chosen
            sensitivity
        h (float): Moving window size relative to histsize. Can be one of 0.25,
            0.5 and 1
        winsize (numpy.ndarray): 2D array with absolute window size. Computed as
            h*histsize
        window (numpy.ndarray): 3D array containing the current values in the
            window

    Args:
        mask (numpy.ndarray): A 2D numpy array containing pixels that should be
            monitored marked as ``1`` and pixels that should be excluded (marked
            as ``0``). Typically a stable forest mask when doing forest disturbance
            monitoring. If no mask is supplied all pixels are considered and
            a mask is created following the ``fit()`` call
        trend (bool): Indicate whether stable period fit is performed with
            trend or not
        harmonic_order (int): The harmonic order of the time-series regression
        x_coords (numpy.ndarray): x coordinates
        y_coords (numpy.ndarray): y coordinates
        sensitivity (float): sensitivity of the monitoring. Lower numbers
            correspond to lower sensitivity. Equivalent to significance level
            'alpha' with which the boundary is computed
        boundary (numpy.ndarray): process boundary for each time series.
            Calculated from alpha and length of time series.
        sigma (numpy.ndarray): Standard deviation for normalized residuals in
            history period
        histsize (numpy.ndarray): Number of non-nan observations in history
            period
        n (numpy.ndarray): Total number of non-nan observations in time-series
        h (float): Moving window size relative to histsize. Can be one of 0.25,
            0.5 and 1
        winsize (numpy.ndarray): 2D array with absolute window size. Computed as
            h*histsize
        window (numpy.ndarray): 3D array containing the current values in the
            window
    """

    def __init__(self, mask=None, trend=True, harmonic_order=2, beta=None,
                 x_coords=None, y_coords=None, process=None, sensitivity=0.05,
                 boundary=None, detection_date=None, sigma=None, histsize=None,
                 n=None, h=0.25, winsize=None, window=None, **kwargs):
        super().__init__(mask=mask,
                         trend=trend,
                         harmonic_order=harmonic_order,
                         beta=beta,
                         x_coords=x_coords,
                         y_coords=y_coords,
                         process=process,
                         boundary=boundary,
                         detection_date=detection_date)
        self.sensitivity = sensitivity
        self.critval = _mosum_ols_test_crit(sensitivity, h=h,
                                            period=10, functional='max')
        self.sigma = sigma
        self.histsize = histsize
        self.n = n
        self.h = h
        self.winsize = winsize
        self.window = window

    def get_process(self):
        return np.nansum(self.window, axis=0)

    def set_process(self, x):
        pass

    process = property(get_process, set_process)

    def fit(self, dataarray, method='ROC', alpha=0.05, **kwargs):
        """Stable history model fitting

        If method ``'ROC'`` is used for fitting, the argument ``alpha`` has
        to be passed.

        Args:
            dataarray (xr.DataArray): xarray Dataarray including the historic
                data to be fitted
            method (string): Regression to use. See ``_fit()`` for info.
            alpha (float): Significance level for ``'ROC'`` stable fit.
            **kwargs: to be passed to ``_fit``
        """
        self.set_xy(dataarray)
        X = self.build_design_matrix(dataarray, trend=self.trend,
                                     harmonic_order=self.harmonic_order)
        self.beta, residuals = self._fit(X, dataarray,
                                         method=method,
                                         alpha=alpha,
                                         **kwargs)

        # histsize is necessary for normalization of residuals,
        # n is necessary for boundary calculation
        self.histsize = np.sum(~np.isnan(residuals), axis=0) \
            .astype(np.uint16)
        self.winsize = np.floor(self.histsize * self.h).astype(np.int16)
        self.n = self.histsize
        self.boundary = np.full_like(self.histsize, np.nan, dtype=np.float32)
        self.sigma = np.nanstd(residuals, axis=0, ddof=X.shape[1])
        # calculate normalized residuals
        residuals_ = residuals / (self.sigma * np.sqrt(self.histsize))
        # TODO self.window can be converted to property to allow for safe
        #   application of scaling factor with getter and setter
        self.window = _mosum_init_window(residuals_, self.winsize)

    def _update_process(self, residuals, is_valid):
        """Update process
        (Isn't actually updating process directly, but is updating the values
        from which the process gets calculated)"""
        # get valid indices
        valid_idx = np.where(is_valid)

        # get indices which need to be changed and write normalized residuals
        change_idx = np.mod(self.n-self.histsize, self.winsize)[valid_idx]
        residuals_norm = residuals / (self.sigma * np.sqrt(self.histsize))
        self.window[change_idx, valid_idx[0], valid_idx[1]] = residuals_norm[valid_idx]

        # calculate boundary
        self.n = self.n + is_valid
        x = self.n / self.histsize
        log_out = np.ones_like(x)
        self.boundary = np.where(is_valid,
                                 self.critval * np.sqrt(
                                     2 * np.log(x, out=log_out,
                                                where=(x > np.exp(1)))),
                                 self.boundary)

    def _detect_break(self):
        """Defines if the current process value is a confirmed break"""
        is_break = np.abs(self.process) > self.boundary
        return is_break
