"""DTO de los endpoints públicos de sistema y seguridad."""
from pydantic import BaseModel, ConfigDict, Field


class HealthOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})

    status: str = Field(description="`ok` while the service is up.")


class ClavePublicaOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "algoritmo": "ML-DSA-65", "estandar": "FIPS 204", "clave_publica_b64": "<1952 bytes en base64>",
        "huella_sha3_256": "<SHA3-256 de la clave pública, en hexadecimal>"}]})

    algoritmo: str = Field(description="Signature algorithm of the data: ML-DSA-65.")
    estandar: str = Field(description="Standard of the algorithm: NIST FIPS 204.")
    clave_publica_b64: str = Field(description="Raw public key (1952 bytes) in base64.")
    huella_sha3_256: str = Field(description="SHA3-256 fingerprint (hexadecimal) of the public key. Compare it with the one published in the README, "
                                             "not with the one this same server hands out.")
