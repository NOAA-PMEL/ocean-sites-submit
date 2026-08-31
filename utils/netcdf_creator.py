from pathlib import Path
import xarray as xr
import numpy as np

#TODO neet to sort out fill_value for when it is None
# TODO: Need to save file path to submission id as "generated" in file table (this most likely needs to happend outside of this class and in the dash app)

class NetcdfCreator:

    STRUCTURAL_VAR_KEYS = {"name", "var_type", "dtype", "dimensions"}
    FILL_KEYS = {"fill_value"}
    ENCODING_ONLY = {"dtype", *FILL_KEYS}

    def __init__(self, metadata_dict: dict, og_netcdf_filepath: str, final_filepath: str):
        """
        metadata_dict:      The formatted metadata dictionary. The output
                            from load_ocean_sites_metadata in 
                            callbacks/extensions/ocean_sites/utils. Formatted the
                            same way as the output from the OceanSitesNetcdfParser.
        og_netcdf_file:     The path to the original netcdf file, so the data can be
                            grabbed and the updated metadata matched with it.
        final_filepath:     The path to where the final generated netCDF file will be saved to.
        """

        self.metadata_dict = metadata_dict
        self.og_netcdf_filepath = Path(og_netcdf_filepath)
        self.final_filepath = Path(final_filepath)
        self.declared_dims = {d["name"]: d for d in self.metadata_dict.get("dimensions", [])}
        self.encoding = {}
        self.output_dataset = xr.Dataset()

    def create_xr_dataset(self):
        
        with xr.open_dataset(self.og_netcdf_filepath, decode_cf=False) as src:

            # Build variables - will add to the self.output_dataset and self.encoding
            self.create_var_metadata(og_xr_dataset=src)

            # Global attributes
            # Flatten attribute/value dicitonary
            extra = self.metadata_dict.get("global_attributes", {}).pop("extra_global_attrs", {})
            flat_global_attrs = {**self.metadata_dict["global_attributes"], **extra}
            self.output_dataset.attrs = flat_global_attrs

            unlimited = [d["name"] for d in self.metadata_dict.get("dimesnsions", []) if d.get("unlimited")]

            self.output_dataset.to_netcdf(
                self.final_filepath,
                format="NETCDF4",
                encoding=self.encoding,
                unlimited_dims=unlimited or None,
            )

            print(f"Generated netCDF file successfully saved to: {self.final_filepath}")


    def create_var_metadata(self, og_xr_dataset: xr.Dataset):

        for var in self.metadata_dict.get("variables", []):
            name = var["name"]
            dims = tuple(var["dimensions"])
            dtype = np.dtype(var["dtype"])

            # Flatten attribute/value dicitonary
            extra = var.pop("extra_attrs", {})
            flat = {**var, **extra}

            # Attributes
            attrs = {
                k: v 
                for k, v in flat.items()
                if k not in self.STRUCTURAL_VAR_KEYS and k not in self.ENCODING_ONLY
            }

            # Fill Value
            fill_value = self._extract_fill_value(var, dtype)

            # verify the variable from metadata dict is in data
            if name not in og_xr_dataset.variables:
                msg = f"no variable named {name!r} in {self.og_netcdf_filepath}"
                raise KeyError(msg)

            dataset_var = og_xr_dataset.variables[name]
            var_values = np.asarray(dataset_var.values)

            # Check that the variable number of dimensions is the same as the number of dimensions captured in the metadata
            if var_values.ndim != len(dims):
                raise ValueError(
                    f"{name!r}: data has {var_values.ndim} dimensions but metadata "
                    f"lists {len(dims)}: {dims}"
                )

            # Check that the declared dimensions (from dimensions nested dict) matches the var_values
            for axis, dim_name in enumerate(dims):
                declared = self.declared_dims.get(dim_name)
                if declared and not declared.get("unlimited"):
                    if var_values.shape[axis] != int(declared["size"]):
                        raise ValueError(
                            f"{name!r}: aixs {axis} has length "
                            f"{var_values.shape[axis]}, but dimension "
                            f"{dim_name!r} is declared as {declared['size']}"
                        )

            # Make the data type of the value match the assigned dtype
            if var_values.dtype != dtype:
                var_values = var_values.astype(dtype)

            # Build the variables directly
            self.output_dataset[name] = xr.Variable(dims, var_values, attrs=attrs)

            # Create the variable encoding
            var_encoding = {"dtype": dtype}
            var_encoding["_FillValue"] = fill_value

            src_encoding = xr_dataset[name].encoding
            if src_encoding.get("zlib"):
                var_encoding["zlib"] = True
                var_encoding["complevel"] = src_encoding.get("complevel", 4)
                var_encoding["shuffle"] = src_encoding.get("shuffle", True)
            if "chunksizes" in src_encoding and src_encoding["chunksizes"]:
                var_encoding["chunksizes"] = src_encoding["chunksizes"]

            self.encoding[name] = var_encoding


    def _extract_fill_value(self, var, dtype):
        """
        Pull the fill value out of a variable dict and cast it to dtype
        """
        for key in self.FILL_KEYS:
            if key in var and var[key] is not None:
                try:
                    return np.dtype(dtype).type(var[key])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"fill value {var[key]!r} for variable {var.get('name')!r} "
                        f" is not compatible with dtype {dtype}"
                    ) from exc
        return None
        