import xarray as xr
import re
import numpy as np
import netCDF4 as nc
from portal_core.utils import app_logger as log
from ocean_sites.utils.variable_types import infer_special_variable_type

class OceanSitesNetcdfParser:

    # SQL Alchemy NetcdfGlobalAttributes fields
    SITE_CODE = "site_code"
    PLATFORM_CODE = "platform_code"
    DATA_MODE = "data_mode"
    DATA_TYPE = "data_type"
    FORMAT_VERSION = "format_version"
    UPDATE_INTERVAL = "update_interval"
    DATE_CREATED = "date_created"
    GEOSPATIAL_LAT_MIN = "geospatial_lat_min"
    GEOSPATIAL_LAT_MAX = "geospatial_lat_max"
    GEOSPATIAL_LON_MIN = "geospatial_lon_min"
    GEOSPATIAL_LON_MAX = "geospatial_lon_max"
    GEOSPATIAL_VERTICAL_MIN = "geospatial_vertical_min"
    GEOSPATIAL_VERTICAL_MAX = "geospatial_vertical_max"
    TIME_COVERAGE_START = "time_coverage_start"
    TIME_COVERAGE_END = "time_coverage_end"
    EXTRA_GLOBAL_ATTRS = "extra_global_attrs"

    # SQL Alchemy NetcdfDimension fields
    SIZE = "size"
    UNLIMITED = "unlimited"
    OBSERVED_VALUES = "observed_values"

    # VAR_TYPE enums for variables
    COORDINATE = "coordinate"
    DATA = "data"
    QC = "qc"
    DM = "dm"
    UNCERTAINTY = "uncertainty"
    
    # SQL Alchemy NetcdfVariable fields
    NAME = "name" # also for dimension table
    VAR_TYPE = "var_type"
    DTYPE = "dtype"
    DIMENSIONS = "dimensions"
    STANDARD_NAME = "standard_name"
    UNITS = "units"
    FILL_VALUE = "fill_value" # required for data variables
    COORDINATES = "coordinates" # required for data variables
    AXIS = "axis" # required for coordinate variables
    FLAG_VALUES = "flag_values" # required for qc variables 
    FLAG_MEANINGS = "flag_meanings" # required for qc variables
    EXTRA_ATTRS = "extra_attrs"

    
    def __init__(self, netcdf_path: str):

        self.netcdf_path = netcdf_path
        self.ds = self.read_netcdf()
        self.metadata_dict = self.create_metadata_dict()

    def create_metadata_dict(self):
        """
        Creates a dictionary of the parsed netCDF metadata. To be
        used in the UI. Looks like:
            {
            "global_attributes": {
                                    "platfrom_code":
                                    "site_code:, 
                                    other required attributes...
                                    "extra_global_attrs": {
                                                            some_global_attr: 1,
                                                            some_global_attr: 2,
                                                            }
                                    },
            "dimensions": [
                                {
                                    "name:,
                                    "size",
                                    "unlimited"
                                },
            },
            "variables": [
                            {
                                "name":
                                other_required_attributes...
                                other required attributes...
                                "extra_attrs": {
                                                        some_var_attr: 1,
                                                        some_var_attr: 2,
                                                        }
                            } 
                            ]
        """
        return {
            "global_attributes": self.read_global_attrs(),
            "dimensions": self.read_dimensions(),
            "variables": self.read_variables()
        }
    
    def read_netcdf(self):

        try:
            ds = xr.open_dataset(self.netcdf_path, decode_times=False, decode_cf=False)
            log.info("NetCDF loaded successfully!")
            return ds
        except FileNotFoundError:
            log.error(f"Error: The file at '{self.netcdf_path}' could not be found. Check the path.")
        except Exception as e:
            # This catches other issues like corruption, missing engine dependencies, etc.
            log.error(f"An error occurred while trying to open the file: {e}")

    def read_global_attrs(self):
        """
        Reads the global attributes and puts in a dictionary that matches the
        SQL Alchemy fields.
        """
        required_global_attrs = [
                            self.SITE_CODE,
                            self.PLATFORM_CODE,
                            self.DATA_MODE,
                            self.DATA_TYPE,
                            self.FORMAT_VERSION,
                            self.UPDATE_INTERVAL,
                            self.DATE_CREATED,
                            self.GEOSPATIAL_LAT_MIN,
                            self.GEOSPATIAL_LAT_MAX,
                            self.GEOSPATIAL_LON_MIN,
                            self.GEOSPATIAL_LON_MAX,
                            self.GEOSPATIAL_VERTICAL_MIN,
                            self.GEOSPATIAL_VERTICAL_MAX,
                            self.TIME_COVERAGE_START,
                            self.TIME_COVERAGE_END,
                        ]
        global_attrs = self.ds.attrs

        # Add required global attributes that exist to the global_info dict
        global_info = {}
        try:
            for required_attr in required_global_attrs:
                if required_attr in global_attrs:
                    global_info[required_attr] = global_attrs.get(required_attr)
                    global_attrs.pop(required_attr)

            # Add additional/optional attributes
            global_info[self.EXTRA_GLOBAL_ATTRS] = global_attrs
            return global_info
  
        except Exception as e:
            log.error(f"An unexpected error occurred while reading netcdf global attributes: {e}")

    def read_dimensions(self):
        """
        Need to use the netCD4 library to extract dimension info for 
        unlimited since I can't do this using xarray.
        """
        try:
            with nc.Dataset(self.netcdf_path) as src:
                dims_list = [
                    {
                        self.NAME: name,
                        self.SIZE: len(dim),
                        self.UNLIMITED: dim.isunlimited()
                    }
                    for name, dim in src.dimensions.items()
                ]
            return dims_list
        except Exception as e:
            log.error(f"An unexpected error occurred while reading netcdf dimensions: {e}")
        
    def read_variables(self):
        variables = []

        # Process coordinate variables first:
        try:
            for name, var in self.ds.coords.items():
                attribute_dict, required_fields = self.peel_out_required_var_attrs(dict(var.attrs), var_type="coordinate")
                var_info = {
                    self.NAME: name,
                    self.VAR_TYPE: self.COORDINATE,
                    self.DTYPE: str(var.dtype),
                    self.DIMENSIONS: list(var.dims),
                    self.EXTRA_ATTRS: attribute_dict,
                    } | required_fields # merge with required fields
                variables.append(var_info)
        except Exception as e:
            log.error(f"An unexpected error occurred while reading netcdf coordinate variables: {e}")


        # Process data and OceanSITES ancillary variables next
        try:
            for name, var in self.ds.data_vars.items():
                var_type = infer_special_variable_type(name) or self.DATA
                attribute_dict, required_fields = self.peel_out_required_var_attrs(
                    dict(var.attrs), var_type=var_type
                )
                var_info = {
                    self.NAME: name,
                    self.VAR_TYPE: var_type,
                    self.DTYPE: str(var.dtype),
                    self.DIMENSIONS: list(var.dims),
                    self.EXTRA_ATTRS: attribute_dict,
                } | required_fields

                if (
                    var_type in (self.QC, self.DM)
                    or "flag_values" in var.attrs
                    or "flag_meanings" in var.attrs
                ):
                    var_info[self.OBSERVED_VALUES] = self._extract_unique_flag_values(var)

                variables.append(var_info)
        except Exception as e:
            log.error(f"An unexpected error occurred while reading netcdf data/qc variables: {e}")
        
        return variables

    def peel_out_required_var_attrs(self, attribute_dict: dict, var_type: str) -> tuple[dict, dict]:
        """
        Peels out any required variable attributes based on the variabe type. Returns two
        dictionaries - the updated attribue_dict with the required fields removed, and a 
        dictionary of the required fields.
            
            attributed_dict:    The dictionary for the attribute key value pairs
                                for the specified variable
            var_type:           The variable type. Either: data, coordinate, or qc
        """
        required_dict = {}

        if var_type == "data":
            # skip coordinates because this info is not in the attribute dict
            if "standard_name" in attribute_dict.keys():
                required_dict[self.STANDARD_NAME] = attribute_dict.get("standard_name")
                attribute_dict.pop("standard_name")
            if "units" in attribute_dict.keys():
                required_dict[self.UNITS] = attribute_dict.get("units")
                attribute_dict.pop("units")
            if "_FillValue" in attribute_dict.keys():
                required_dict[self.FILL_VALUE] = attribute_dict.get("_FillValue")
                attribute_dict.pop("_FillValue")
            if "coordinates" in attribute_dict.keys():
                required_dict[self.COORDINATES] = attribute_dict.get("coordinates")
                attribute_dict.pop("coordinates")
        elif var_type == "coordinate":
            if "standard_name" in attribute_dict.keys():
                required_dict[self.STANDARD_NAME] = attribute_dict.get("standard_name")
                attribute_dict.pop("standard_name")
            if "units" in attribute_dict.keys():
                required_dict[self.UNITS] = attribute_dict.get("units")
                attribute_dict.pop("units")
            if "axis" in attribute_dict.keys():
                required_dict[self.AXIS] = attribute_dict.get("axis")
                attribute_dict.pop("axis")
        elif var_type == "qc":
            if "units" in attribute_dict.keys():
                required_dict[self.UNITS] = attribute_dict.get("units")
                attribute_dict.pop("units")
            if "_FillValue" in attribute_dict.keys():
                required_dict[self.FILL_VALUE] = attribute_dict.get("_FillValue")
                attribute_dict.pop("_FillValue")
            if "flag_values" in attribute_dict.keys():
                required_dict[self.FLAG_VALUES] = self._format_flag_values(
                    attribute_dict.get("flag_values")
                )
                attribute_dict.pop("flag_values")
            if "flag_meanings" in attribute_dict.keys():
                required_dict[self.FLAG_MEANINGS] = attribute_dict.get("flag_meanings")
                attribute_dict.pop("flag_meanings")
        elif var_type == "dm":
            if "flag_values" in attribute_dict:
                required_dict[self.FLAG_VALUES] = self._format_flag_values(
                    attribute_dict.pop("flag_values")
                )
            if "flag_meanings" in attribute_dict:
                required_dict[self.FLAG_MEANINGS] = attribute_dict.pop("flag_meanings")
        elif var_type == "uncertainty":
            if "units" in attribute_dict:
                required_dict[self.UNITS] = attribute_dict.pop("units")
            if "_FillValue" in attribute_dict:
                required_dict[self.FILL_VALUE] = attribute_dict.pop("_FillValue")


        return attribute_dict, required_dict

    @staticmethod
    def _format_flag_values(value) -> str:
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, (list, tuple)):
            values = value
        else:
            values = [value]
        return ", ".join(
            item.decode() if isinstance(item, bytes) else str(item)
            for item in values
        )

    _FILL_ATTR_RE = re.compile(r"^_?fill_?value$|^missing_?value$", re.IGNORECASE)

    @classmethod
    def _extract_unique_flag_values(cls, var) -> list:
        """Return sorted unique non-fill values from an xarray DataArray (decode_cf=False)."""
        data = np.asarray(var.values).ravel()

        # Collect all fill / missing values using flexible key matching
        for key, raw_fill in var.attrs.items():
            if not cls._FILL_ATTR_RE.match(str(key)) or raw_fill is None or not data.size:
                continue
            fill_vals = (
                raw_fill.tolist() if hasattr(raw_fill, "tolist") else raw_fill
            )
            if not isinstance(fill_vals, (list, tuple)):
                fill_vals = [fill_vals]
            for fv in fill_vals:
                if np.issubdtype(data.dtype, np.floating) and isinstance(fv, (float, np.floating)) and np.isnan(fv):
                    data = data[~np.isnan(data)]
                else:
                    data = data[data != fv]

        if np.issubdtype(data.dtype, np.floating) and data.size:
            data = data[~np.isnan(data)]

        if data.size == 0:
            return []

        result = []
        for val in np.unique(data):
            if isinstance(val, (bytes, np.bytes_)):
                decoded = val.decode("utf-8", errors="replace").strip("\x00 ")
                if decoded:
                    result.append(decoded)
            elif isinstance(val, np.integer):
                result.append(int(val))
            elif isinstance(val, np.floating):
                result.append(int(val) if val.is_integer() else float(val))
            else:
                result.append(str(val))
        return result