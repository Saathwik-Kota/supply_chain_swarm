"""
SQLAlchemy ORM models for the Emergency Response Supply Chain Swarm.

Tables:
  - Warehouses: Regional warehouse nodes with geo-coordinates
  - Inventory: Stock levels per supply category per warehouse
  - Fleet: Available vehicles with status tracking
  - Dispatches: Executed relief dispatch orders with manifests
  - TelemetryLogs: Historical weather/hazard telemetry records
  - HazardZones: Active hazard zones with bounding polygons
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Boolean,
    JSON,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


class Warehouse(Base):
    """Regional warehouse / logistics hub node."""

    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    capacity_tier: Mapped[str] = mapped_column(
        String(16), nullable=False, default="primary"
    )
    address: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    inventory_items = relationship("Inventory", back_populates="warehouse", cascade="all, delete-orphan")
    fleet_vehicles = relationship("Fleet", back_populates="warehouse", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_warehouse_city", "city"),
        Index("idx_warehouse_region", "region"),
        Index("idx_warehouse_coords", "latitude", "longitude"),
    )

    def __repr__(self) -> str:
        return f"<Warehouse(id={self.id}, name='{self.name}', city='{self.city}')>"


class Inventory(Base):
    """Stock levels for a specific supply category at a warehouse."""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    warehouse_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="unit")
    weight_per_unit_kg: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    min_reserve: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    last_restocked: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    warehouse = relationship("Warehouse", back_populates="inventory_items")

    __table_args__ = (
        Index("idx_inventory_warehouse", "warehouse_id"),
        Index("idx_inventory_category", "category"),
        Index("idx_inventory_warehouse_category", "warehouse_id", "category", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"<Inventory(id={self.id}, warehouse_id={self.warehouse_id}, "
            f"category='{self.category}', qty={self.quantity})>"
        )


class Fleet(Base):
    """Fleet vehicles assigned to warehouse nodes."""

    __tablename__ = "fleet"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    warehouse_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )
    vehicle_type: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    capacity_kg: Mapped[float] = mapped_column(Float, nullable=False)
    avg_speed_kmh: Mapped[float] = mapped_column(Float, nullable=False, default=60)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="available"
    )  # available, dispatched, maintenance, returning
    current_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fuel_type: Mapped[str] = mapped_column(String(20), nullable=False, default="diesel")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    warehouse = relationship("Warehouse", back_populates="fleet_vehicles")

    __table_args__ = (
        Index("idx_fleet_warehouse", "warehouse_id"),
        Index("idx_fleet_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<Fleet(id={self.id}, vehicle_id='{self.vehicle_id}', "
            f"type='{self.vehicle_type}', status='{self.status}')>"
        )


class Dispatch(Base):
    """Executed relief dispatch orders with full manifest."""

    __tablename__ = "dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dispatch_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_warehouse_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouses.id"), nullable=False
    )
    vehicle_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    destination_name: Mapped[str] = mapped_column(String(256), nullable=False)
    destination_lat: Mapped[float] = mapped_column(Float, nullable=False)
    destination_lon: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending, dispatched, in_transit, delivered, failed
    items_manifest: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    total_weight_kg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    route_geometry: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    route_distance_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    route_duration_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    eta_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hazard_avoided: Mapped[bool] = mapped_column(Boolean, default=False)
    detour_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent_reasoning_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_dispatch_status", "status"),
        Index("idx_dispatch_warehouse", "source_warehouse_id"),
        Index("idx_dispatch_created", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Dispatch(id={self.id}, dispatch_id='{self.dispatch_id}', "
            f"status='{self.status}')>"
        )


class TelemetryLog(Base):
    """Historical weather and hazard telemetry records."""

    __tablename__ = "telemetry_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hub_name: Mapped[str] = mapped_column(String(128), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    temperature_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precipitation_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wind_speed_kmh: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wind_gusts_kmh: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weather_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weather_description: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    visibility_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_hazardous: Mapped[bool] = mapped_column(Boolean, default=False)
    hazard_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    severity: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # low, medium, high, critical
    raw_api_response: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_telemetry_hub", "hub_name"),
        Index("idx_telemetry_hazard", "is_hazardous"),
        Index("idx_telemetry_time", "recorded_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<TelemetryLog(id={self.id}, hub='{self.hub_name}', "
            f"hazardous={self.is_hazardous})>"
        )


class HazardZone(Base):
    """Active hazard zones with geographic boundaries."""

    __tablename__ = "hazard_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    hazard_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="high")
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    center_lon: Mapped[float] = mapped_column(Float, nullable=False)
    radius_km: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    polygon_coords: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="open_meteo")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_hazard_active", "is_active"),
        Index("idx_hazard_type", "hazard_type"),
        Index("idx_hazard_coords", "center_lat", "center_lon"),
    )

    def __repr__(self) -> str:
        return (
            f"<HazardZone(id={self.id}, name='{self.name}', "
            f"type='{self.hazard_type}', active={self.is_active})>"
        )

