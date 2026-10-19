from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import UserContext, get_current_user
from app.database import Base, get_db
from app.models import Project
from app.process_engineering import router


def make_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/engineering.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine); factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Project(id="p", name="P", disease="D", owner_id="owner")); db.commit()
    app = FastAPI(); app.include_router(router)
    def connection():
        with factory() as db: yield db
    app.dependency_overrides[get_db] = connection
    app.dependency_overrides[get_current_user] = lambda: UserContext("owner", "owner", "researcher")
    return TestClient(app)


def test_binary_vle_supports_ideal_and_nrtl(tmp_path):
    client = make_client(tmp_path); payload = {
        "temperature_c": 78, "pressure_kpa": 101.325, "model": "ideal",
        "components": [
            {"name": "乙醇", "mole_fraction": .5, "antoine_a": 8.20417, "antoine_b": 1642.89, "antoine_c": 230.3},
            {"name": "水", "mole_fraction": .5, "antoine_a": 8.07131, "antoine_b": 1730.63, "antoine_c": 233.426},
        ],
    }
    result = client.post("/api/v1/drug-discovery/projects/p/process/engineering/thermodynamics", json=payload)
    assert result.status_code == 200, result.text
    body = result.json(); assert 60 < body["bubble_pressure_kpa"] < 100
    assert abs(sum(x["y"] for x in body["components"]) - 1) < .001
    payload.update({"model": "nrtl", "nrtl_tau12": .3, "nrtl_tau21": .2})
    assert client.post("/api/v1/drug-discovery/projects/p/process/engineering/thermodynamics", json=payload).json()["components"][0]["gamma"] > 1


def test_arrhenius_fit_and_target_time(tmp_path):
    payload = {"model": "first", "initial_concentration_mol_l": 1, "target_temperature_c": 60, "target_conversion_pct": 95,
               "observations": [{"temperature_c": 40, "time_min": 120, "conversion_pct": 55}, {"temperature_c": 50, "time_min": 80, "conversion_pct": 65}, {"temperature_c": 60, "time_min": 50, "conversion_pct": 75}]}
    body = make_client(tmp_path).post("/api/v1/drug-discovery/projects/p/process/engineering/kinetics", json=payload).json()
    assert body["activation_energy_kj_mol"] > 0 and body["target"]["predicted_time_min"] > 0
    assert body["target"]["k_unit"] == "1/min"


def test_peng_robinson_gas_density(tmp_path):
    payload = {"temperature_c": 25, "pressure_bar": 100, "components": [
        {"name": "氮气", "mole_fraction": .8, "critical_temperature_k": 126.2, "critical_pressure_bar": 33.98, "acentric_factor": .0372, "molecular_weight_g_mol": 28.0134},
        {"name": "二氧化碳", "mole_fraction": .2, "critical_temperature_k": 304.13, "critical_pressure_bar": 73.77, "acentric_factor": .224, "molecular_weight_g_mol": 44.01},
    ]}
    response = make_client(tmp_path).post("/api/v1/drug-discovery/projects/p/process/engineering/gas-eos", json=payload)
    assert response.status_code == 200, response.text
    body = response.json(); assert 0 < body["compressibility_factor_z"] < 2
    assert body["gas_density_kg_m3"] > 0 and body["version"] == "peng-robinson-screen-v1"


def test_equipment_safety_and_solids_calculators(tmp_path):
    client = make_client(tmp_path)
    equipment = {"batch_mass_kg": 1200, "mixture_density_kg_l": .9, "fill_fraction": .7, "agitator_power_kw_m3": 1.5,
                 "heat_duty_kw": 80, "overall_u_w_m2_k": 350, "lmtd_k": 30, "filtration_volume_l": 1200,
                 "filtration_flux_l_m2_h": 150, "filtration_time_h": 4, "liquid_flow_m3_h": 12, "liquid_velocity_m_s": 1.5,
                 "gas_flow_nm3_h": 0, "gas_velocity_m_s": 15, "relative_volatility": 2.4, "distillate_light_fraction": .95, "bottoms_light_fraction": .05}
    e = client.post("/api/v1/drug-discovery/projects/p/process/engineering/equipment", json=equipment).json()
    assert e["reactor"]["minimum_geometric_volume_m3"] > e["reactor"]["working_volume_m3"]
    assert e["heat_transfer"]["required_area_m2"] > 0 and e["distillation"]["fenske_minimum_theoretical_stages"] > 0
    safety = {"reaction_enthalpy_kj_mol": -120, "reactive_moles": 4000, "reaction_mass_kg": 1200, "heat_capacity_kj_kg_k": 2.5,
              "start_temperature_c": 40, "maximum_safe_temperature_c": 100, "decomposition_onset_c": 130, "accumulated_fraction": .35,
              "maximum_dosing_rate_mol_min": 40, "available_cooling_kw": 30, "generated_gas_nm3": 10}
    s = client.post("/api/v1/drug-discovery/projects/p/process/engineering/safety", json=safety).json()
    assert s["risk_level"] == "高" and any("DIERS" in x for x in s["flags"])
    solids = {"solvent_mass_kg": 800, "hot_solubility_g_100g_solvent": 30, "cold_solubility_g_100g_solvent": 6,
              "seed_loading_pct": .5, "target_d50_um": 150, "slurry_volume_l": 1000, "measured_filter_flux_l_m2_h": 120,
              "target_filtration_time_h": 4, "wet_cake_mass_kg": 220, "initial_moisture_pct": 25, "final_moisture_pct": 1,
              "latent_heat_kj_kg": 2257, "dryer_efficiency_pct": 50}
    so = client.post("/api/v1/drug-discovery/projects/p/process/engineering/solids", json=solids).json()
    assert so["crystallization"]["theoretical_crystal_kg"] == 192
    assert so["filtration"]["design_area_m2"] > 0 and so["drying"]["minimum_thermal_energy_kwh"] > 0
