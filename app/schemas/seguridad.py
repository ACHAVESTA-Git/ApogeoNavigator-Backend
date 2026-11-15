"""DTO de los endpoints públicos de sistema y seguridad."""
from pydantic import BaseModel, ConfigDict, Field


class HealthOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})

    status: str = Field(description="`ok` mientras el servicio está en marcha.")


class ClavePublicaOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "algoritmo": "ML-DSA-65", "estandar": "FIPS 204", "clave_publica_b64": "<1952 bytes en base64>",
        "huella_sha3_256": "<SHA3-256 de la clave pública, en hexadecimal>"}]})

    algoritmo: str = Field(description="Algoritmo de firma de los datos: ML-DSA-65.")
    estandar: str = Field(description="Estándar del algoritmo: NIST FIPS 204.")
    clave_publica_b64: str = Field(description="Clave pública cruda (1952 bytes) en base64.")
    huella_sha3_256: str = Field(description="Huella SHA3-256 (hexadecimal) de la clave pública. Compárala con la publicada en el README, "
                                             "no con la que sirve este mismo servidor.")
