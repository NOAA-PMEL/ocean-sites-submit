import enum
import re
from typing import Optional
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, String, ForeignKey, Enum, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship, backref
from sqlalchemy.dialects.postgresql import JSONB

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from portal_core.models import Base, OadsMetadataSchema

#TODO: does shape field need to be in the netcdfVariable table? 

# ---- Global Variables -------

class DataMode(str, enum.Enum):
    REAL_TIME = "R"
    PROVISIONAL = "P"
    DELAYED = "D"
    MIXED = "M"

    @property
    def label(self):
        labels = {
            "R": "Real-time data",
            "P": "Provisional data",
            "D": "Delayed mode data",
            "M": "Mixed",
        }
        return labels[self.value]

data_mode_db_enum = Enum(
    DataMode,
    native_enum=True,
    name="data_mode_enum",
    values_callable=lambda x: [item.value for item in x],
)

class DataType(str, enum.Enum):
    PROFILE = "OceanSITES profile data"
    TIME_SERIES = "OceanSITES time-series data"
    TRAJECTORY = "OceanSITES trajectory data"

    @property
    def label(self):
        return self.label

data_type_db_enum = Enum(
    DataType,
    native_enum=True,
    name="data_type_enum",
    values_callable=lambda x: [item.value for item in x]
)

class FormatVersion(str, enum.Enum):
    V1_1 = "1.1"
    V1_3 = "1.3"
    V1_5 = "1.5"

    @property
    def label(self):
        return self.value

format_version_db_enum = Enum(
    FormatVersion,
    native_enum=True,
    name="format_version_enum",
    values_callable=lambda x: [item.value for item in x],
)

class NetcdfGlobalAttributes(Base):
    # all the required global variables in a netCDF for Ocean Sites. This table is a 
    # 1:1 mapping with OadsMetadata table so we don't edit the re-usable OadsMetadata table.
    # extra_global_attrs is a jsonb for any additional (non-required attributes)
    __tablename__ = "netcdf_global_attributes"

    id: Mapped[int] = mapped_column(primary_key=True)
    oads_metadata_id: Mapped[int] = mapped_column(
        ForeignKey("oads_metadata.id" ,ondelete="CASCADE"), 
        unique=True, # enforces one-to-one
        index=True
        )

    # -- Identification --
    site_code: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Site Code"}
    )
    platform_code: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Platform Code"}
    )
    data_mode: Mapped[Optional[DataMode]] = mapped_column(
        data_mode_db_enum, nullable=True, info={"label": "Data Mode"}
    )
    data_type: Mapped[Optional[DataType]] = mapped_column(
        data_type_db_enum, nullable=True, info={"label": "Data Type"}
    )
    format_version: Mapped[Optional[FormatVersion]] = mapped_column(
        format_version_db_enum, nullable=True, info={"label": "Format Version"}
    )
    update_interval: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Update Interval"}
    )
    date_created: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Date Created"}
    )

    # -- Spatial Extents --
    geospatial_lat_min: Mapped[Optional[Decimal]] = mapped_column(
        Numeric, nullable=True, info={"label": "Geospatial Latitude Min"}
    )
    geospatial_lat_max: Mapped[Optional[Decimal]] = mapped_column(
        Numeric, nullable=True, info={"label": "Geospatial Latitude Max"}
    )
    geospatial_lon_min: Mapped[Optional[Decimal]] = mapped_column(
        Numeric, nullable=True, info={"label": "Geospatial Longitude Min"}
    )
    geospatial_lon_max: Mapped[Optional[Decimal]] = mapped_column(
        Numeric, nullable=True, info={"label": "Geospatial Longitude Max"}
    )
    geospatial_vertical_min: Mapped[Optional[Decimal]] = mapped_column(
        Numeric, nullable=True, info={"label": "Geospatial Vertical Min"}
    )
    geospatial_vertical_max: Mapped[Optional[Decimal]] = mapped_column(
        Numeric, nullable=True, info={"label": "Geospatial Vertical Max"}
    )

    # -- Temporal Extents --
    time_coverage_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, info={"label": "Time Coverage Start"}
    )
    time_coverage_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, info={"label": "Time Coverage End"}
    )

    # -- Any additional global attributes not in the required set --
    extra_global_attrs: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, info={"label": "Extra Global Attributes"}
    )

    # This is strictly for the UI to compare oringally uploaded metadata to anything that's been edited
    # This contains the output of the NetCDF parser dictionary (so will include variables as well). Not ideal
    # data modeling, but works fine for our purposes.
    og_netcdf_metadata: Mapped[Optional[dict]] =mapped_column(
        JSONB, nullable=True, info={"label": "Original netCDF metadata"}
    )


    # -- Relationship back to OadsMetadata (injected via backref) --
    oads_metadata: Mapped["OadsMetadata"] = relationship(
        "OadsMetadata",
        backref=backref( # using backref instead of backpopulates and both tables because we don't want to edit the re-usable OadsMetadata table.
            "netcdf_global_attributes",
            uselist=False, # one-to-one
            passive_deletes=True
        ),
    )

class NetcdfGlobalAttributesSchema(BaseModel):
    """Strict schema for validation before final submission"""
    model_config = ConfigDict(from_attributes=True)

    site_code: str
    platform_code: str
    data_mode: DataMode
    data_type: DataType
    format_version: FormatVersion
    update_interval: str
    date_created: str
    geospatial_lat_min: float = Field(..., ge=-90, le=90)
    geospatial_lat_max: float = Field(..., ge=-90, le=90)
    geospatial_lon_min: float = Field(..., ge=-180, le=180)
    geospatial_lon_max: float = Field(..., ge=-180, le=180)
    geospatial_vertical_min: float
    geospatial_vertical_max: float
    time_coverage_start: datetime
    time_coverage_end: datetime
    extra_global_attrs: Optional[dict] = None

    @field_validator("time_coverage_start", "time_coverage_end", "date_created", mode="before")
    @classmethod
    def validate_iso8061_utc(cls, v: str) -> str:
        """
        Validates ISO 8601 combined date and time in UTC, requiring a trailing 'Z'

        Accepted formats:
            - YYYY-MM-DDTHH:MM:SSZ
            - YYYY-MM-DDTHH:MM:SS.fffffZ
        """
        ISO8601_UTC_PATTERN = re.compile(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
        )
        if not isinstance(v, str):
            raise ValueError("datetime must be provided as a string in ISO 8601 UTC format")
        if not ISO8601_UTC_PATTERN.match(v):
            raise ValueError(f"'{v}' is not a valid ISO 8601 UTC datetime. "
                            "Expected format: YYYY-MM-DDTHH:MM:SSZ orYYYY-MM-DDTHH:MM:SS.fffffZ")
        return v

    @field_validator("update_interval", mode="before")
    @classmethod
    def validate_update_interval(cls, v: str) -> str:
        """
        Validates that update_interval is either:
            - "void" for data no updated on a schedule
            - A valid ISO 8601 duration in the form of PnYyMnDtnHnM
              where any zero-valued elements may be omitted.
        Valid examples: P1Y, P6M, PT12H, P1DT6H, void
        """
        if not isinstance(v, str):
            raise ValueError("update_interval must be a string")
        if v == "void":
            return v
        ISO8601_DURATION_PATTERN = re.compile(
            r"^P"
            r"(?!T?$)" # must have at least one designator after P
            r"(\d+Y)?(\d+M)?(\d+D)?" # years, months, days (all optional)
            r"(T(?!$)(\d+H)?(\d+M)?)?" # T section optional, but if present must not be empty
            r"$"
        )
        if not ISO8601_DURATION_PATTERN.match(v):
            raise ValueError(
                f"'{v}' is not a valid update_interval. "
                "Use ISO 8601 duration format (e.g. PP1Y, P6M, PT12H, P1DT6H) "
                "or 'void' for data not updated on a schedule."
            )
        return v
            
    @model_validator(mode="after")
    def check_spatial_bounds(self):
        if self.geospatial_lat_min >= self.geospatial_lat_max:
            raise ValueError("geospatial_lat_min must be less than geospatial_lat_max")
        if self.geospatial_lon_min >= self.geospatial_lon_max:
            raise ValueError("geospatial_lon_min must be less than geospatial_lon_max")
        return self

# ---- Dimensions -------

class NetcdfDimension(Base):
    __tablename__ = "netcdf_dimension"

    id: Mapped[int] = mapped_column(primary_key=True)
    oads_metadata_id: Mapped[int] = mapped_column(
        ForeignKey("oads_metadata.id", ondelete="CASCADE"), 
        index=True
        )

    name: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Dimension Name"}
    )
    size: Mapped[Optional[int]] = mapped_column(
        nullable=True, info={"label": "Size"}
    )
    unlimited: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True, default=False, info={"label": "Unlimited"}
    )

    # --- Relationship back to OadsMetadata (injected via backref) ---
    oads_metadata: Mapped["OadsMetadata"] = relationship(
    "OadsMetadata",
    backref=backref( # using backref instead of backpopulates and both tables because we don't want to edit the re-usable OadsMetadata table.
        "netcdf_dimensions",
        cascade="all, delete-orphan",
        passive_deletes=True
    ),
)

class NetcdfDimensionSchema(BaseModel):
    """
    Strict schema for validation - dimensions are fully known once the file is parsed.
    """
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int]
    name: str
    size: int = Field(..., gt=0)
    unlimited: bool = False

    
class VarType(str, enum.Enum):
    DATA = "data"
    COORDINATE = "coordinate"
    QC = "qc"
    DM = "dm"
    UNCERTAINTY = "uncertainty"
    
    @property
    def label(self):
        labels = {
            "data": "Data Variable",
            "coordinate": "Coordinate Variable",
            "qc": "QC Variable",
            "dm": "DM Variable",
            "uncertainty": "Uncertainty Variable"
        }
        return labels[self.value]

var_type_db_enum = Enum(
    VarType,
    native_enum=True,
    name="var_type_enum",
    values_callable=lambda x: [item.value for item in x],
)

class NetcdfVariable(Base):
    __tablename__ = "netcdf_variable"

    id: Mapped[int] = mapped_column(primary_key=True)
    oads_metadata_id: Mapped[int] = mapped_column(
        ForeignKey("oads_metadata.id", ondelete="CASCADE"), 
        index=True
        )

    # --- Structure (derived from file, read-only)
    name: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Variable Name"}
    )
    var_type: Mapped[Optional[VarType]] = mapped_column(
        var_type_db_enum, nullable=True, info={"label": "Variable Type"}
    )
    dtype: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Data Type"}
    )
    # shape: Mapped[Optional[str]] = mapped_column(
    #     String, nullable=True, info={"label": "Shape"} # e.g. "8760 x 450 x 800"
    # )
    dimensions: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Dimensions"} # e.g. "time lat lon"
    )

    # --- Attributes shared across var types ----
    standard_name: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Standard Name"}
    )
    units: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Units"}
    )

    # ---- Data variable attributes
    fill_value: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Fill Value"}
    )
    coordinates: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Coordinates"} # CF space-separate
    )

    # --- Coordinate variable attributes -----
    axis: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Axis"} # . X, Y, Z, or T
    )

    # --- QC variable attributes ---
    flag_values: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Flag Values"}
    )
    flag_meanings: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, info={"label": "Flag Meanings"}
    )

    # --- Any additional attributes not in the standard set
    extra_attrs: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, info={"label": "Extra Attributes"}
    )

    # # --- Provenance Tracking ----
    # source: Mapped[Optional[dict]] = mapped_column(
    #     String, nullable=True, info={"label": "Source"} # 'file' or 'manual'
    # )
    # reviewed: Mapped[Optional[bool]] = mapped_column(
    #     Boolean, nullable=True, info={"label": "Reviewed"}
    # )

    # --- Relationship back to OadsMetadata (injected via backref) ---
    oads_metadata: Mapped["OadsMetadata"] = relationship(
    "OadsMetadata",
    backref=backref( # using backref instead of backpopulates and both tables because we don't want to edit the re-usable OadsMetadata table.
        "netcdf_variables",
        cascade="all, delete-orphan",
        passive_deletes=True
    ),
)

# Variable pydantic schemas have been split into three based on the three var_types (data, coordinate, and qc)
class NetcdfVariableBaseSchema(BaseModel):
    """Fields common to all variable types"""
    
    # id: int
    name: str
    var_type: VarType
    dtype: str
    dimensions: str
    extra_attrs: Optional[dict] = None

class NetcdfCoordinateVariableSchema(NetcdfVariableBaseSchema):
    """Strict schema for coordinate variables"""
    var_type: VarType = VarType.COORDINATE
    standard_name: str
    units: str
    axis: str

class NetcdfDataVariableSchema(NetcdfVariableBaseSchema):
    """Strict schema for data variables"""
    var_type: VarType = VarType.DATA
    standard_name: str
    units: str
    fill_value: str
    coordinates: str

class NetcdfQCVariableSchema(NetcdfVariableBaseSchema):
    """Strict schema for QC variables"""
    var_type: VarType = VarType.QC
    units: Optional[str] = None
    fill_value: Optional[str] = None
    flag_values: str
    flag_meanings: str

class NetcdfDMVariableSchema(NetcdfVariableBaseSchema):
    """Strict schema for data-mode variables"""
    var_type: VarType = VarType.DM
    flag_values: str
    flag_meanings: str

class NetcdfUncertaintyVariableSchema(NetcdfVariableBaseSchema):
    """Strict schema for uncertainty variables"""
    var_type: VarType = VarType.UNCERTAINTY
    units: str
    fill_value: float

class OceanSitesOadsMetadataSchema(OadsMetadataSchema):
    netcdf_global_attributes: Optional[list[NetcdfGlobalAttributesSchema]] = []
    netcdf_dimensions: Optional[list[NetcdfDimensionSchema]] = []
    netcdf_variables: Optional[list[NetcdfVariableBaseSchema]] = []
    
    

    