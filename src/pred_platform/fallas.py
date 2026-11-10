"""Cómo se muestra un error: causa en español, qué hacer, desglose y detalle para soporte.

Es la única puerta entre un ``ErrorInfo`` (de una tarea, una familia, una ejecución o una
ingesta) y la pantalla. El título y la acción salen del catálogo (``mensajes``); el desglose,
de los hechos del error (``ErrorInfo.facts``, contrato 1.3.0, sección 11.1) o, en corridas
anteriores, de su texto (``hechos_legados``). El texto del motor nunca se muestra a la vista:
queda en ``soporte``, que la interfaz ofrece plegado y copiable.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError

from pred_platform import mensajes
from pred_platform.app.presentation import format_count, stage_label
from pred_platform.contract import models
from pred_platform.hechos_legados import hechos_del_texto

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CausaFalla:
    """Una causa de descarte de la búsqueda: cuántos ensayos y un ejemplo en palabras."""

    etiqueta: str
    cantidad: int
    ejemplo: str | None


@dataclass(frozen=True, slots=True)
class Falla:
    """Lo que la interfaz muestra de un error.

    ``titulo`` y ``accion`` son la línea visible; ``resumen`` y ``causas`` el desglose;
    ``soporte`` el texto técnico completo (código, etapa, excepción y mensaje del motor).
    """

    codigo: str
    titulo: str
    accion: str | None
    ubicacion: str | None
    resumen: str | None
    causas: tuple[CausaFalla, ...]
    soporte: str
    reintentable: bool

    @property
    def tiene_desglose(self) -> bool:
        return bool(self.resumen or self.causas)


# Título para un código que esta versión de la plataforma no conoce.
TITULO_DESCONOCIDO: Final = "El motor informó un error que esta versión no reconoce."
ACCION_DESCONOCIDA: Final = "Copie el detalle para soporte y compártalo con quien administra PRED."

_CAUSAS: Final[Mapping[str, str]] = {
    "ajuste_fallido": "El modelo no pudo ajustarse",
    "escalon": "Se descartó por rendir peor que las demás",
    "poda_semantica": "El pronóstico de prueba no era válido",
    "configuracion_invalida": "La configuración no cumplía una regla de la búsqueda",
    "ninguna_ventana_convergio": "Ninguna ventana de validación dio un error medible",
    "sin_motivo_registrado": "El motor no registró el motivo",
}
_CAUSA_DESCONOCIDA: Final = "Otra causa del motor"

# Por qué no se ajustó un modelo; ``{modelo}`` es su nombre (por ejemplo su orden SARIMA).
_RAZONES_AJUSTE: Final[Mapping[str, str]] = {
    "raiz_en_borde": "{modelo} dejó una raíz AR/MA al borde de la región estable",
    "sin_convergencia": "el ajuste de {modelo} no convergió",
    "error_biblioteca": "la biblioteca rechazó el ajuste de {modelo}",
    "pronostico_fallido": "{modelo} no produjo el pronóstico",
    "forma_cuantiles": "{modelo} no entregó los cuantiles esperados",
    "cuantil_ausente": "{modelo} no entregó el cuantil que se usa como pronóstico",
}
_REGLAS_PODA: Final[Mapping[str, str]] = {
    "prediccion_nula": "el pronóstico fue cero en todo el horizonte aunque la serie tenía demanda",
    "prediccion_no_finita": "el pronóstico tuvo valores no numéricos o infinitos",
    "demanda_negativa": "el pronóstico tuvo demanda negativa",
}
_RESTRICCIONES: Final[Mapping[str, str]] = {
    "orden_total_acotado": "la suma de los órdenes AR y MA superaba el máximo permitido",
    "no_degenerada": "el modelo no tenía ningún término AR ni MA",
    "costo_acotado": "el tamaño de la red superaba el máximo permitido",
}


def describir_falla(error: models.ErrorInfo | Mapping[str, Any]) -> Falla:
    """La presentación de ``error`` (un ``ErrorInfo`` o su diccionario)."""
    datos = error.model_dump() if isinstance(error, models.ErrorInfo) else dict(error)
    codigo = str(datos.get("code") or "")
    detalle = datos.get("detail")
    descripcion = mensajes.describir(datos)
    conocido = mensajes.mensaje(codigo) is not None
    hechos = hechos_de(datos.get("facts"), detalle)
    resumen, causas = _desglose(hechos)
    titulo = descripcion.titulo if conocido else TITULO_DESCONOCIDO
    # Un detalle que repite el título no dice nada nuevo, ni siquiera a soporte.
    if isinstance(detalle, str) and detalle.strip() == titulo:
        detalle = None
    return Falla(
        codigo=codigo,
        titulo=titulo,
        accion=descripcion.accion if conocido else ACCION_DESCONOCIDA,
        ubicacion=descripcion.ubicacion,
        resumen=resumen,
        causas=causas,
        soporte=texto_soporte(
            codigo,
            etapa=datos.get("stage"),
            excepcion=datos.get("source_exception"),
            detalle=detalle,
            campo=datos.get("field"),
        ),
        reintentable=descripcion.reintentable,
    )


def hechos_de(facts: object, detalle: object) -> models.ErrorFacts | None:
    """Los hechos del error: los del motor si cumplen el contrato; si no, los de su texto."""
    for candidato in (facts, hechos_del_texto(detalle if isinstance(detalle, str) else None)):
        if not candidato:
            continue
        try:
            return models.ErrorFacts.model_validate(candidato)
        except ValidationError as exc:
            _log.warning("hechos de error descartados: no cumplen ErrorFacts (%s)", exc)
    return None


def texto_soporte(
    codigo: str,
    *,
    etapa: object = None,
    excepcion: object = None,
    detalle: object = None,
    campo: object = None,
    bloqueos: tuple[str, ...] | list[str] = (),
) -> str:
    """El texto técnico que se copia para soporte, una línea por dato."""
    lineas = [f"Código: {codigo or 'sin código'}"]
    if etapa:
        nombre = "plataforma" if etapa == "platform" else stage_label(str(etapa))
        lineas.append(f"Etapa: {nombre} ({etapa})" if nombre != etapa else f"Etapa: {etapa}")
    if excepcion:
        lineas.append(f"Excepción: {excepcion}")
    if campo:
        lineas.append(f"Campo: {campo}")
    if bloqueos:
        lineas.append(f"Depende de: {', '.join(bloqueos)}")
    if detalle:
        lineas.append(f"Detalle: {detalle}")
    return "\n".join(lineas)


# ------------------------------------------------------------------------------- desglose
def _desglose(hechos: models.ErrorFacts | None) -> tuple[str | None, tuple[CausaFalla, ...]]:
    if hechos is None:
        return None, ()
    if hechos.search is not None:
        return _resumen_busqueda(hechos.search), tuple(
            CausaFalla(
                etiqueta=_CAUSAS.get(causa.cause, _CAUSA_DESCONOCIDA),
                cantidad=causa.count,
                ejemplo=_ejemplo(causa.cause, causa.example),
            )
            for causa in hechos.search.causes
        )
    partes = []
    if hechos.fit is not None:
        partes.append(_frase(_ajuste(hechos.fit)))
    if hechos.history is not None:
        partes.append(_historia(hechos.history))
    return (" ".join(partes) or None), ()


def _resumen_busqueda(busqueda: models.SearchFacts) -> str:
    total = busqueda.failed + busqueda.pruned
    if total == 0:
        frase = "Ninguna configuración llegó a probarse."
    else:
        partes = []
        if busqueda.failed:
            partes.append(
                f"{format_count(busqueda.failed)} {_plural(busqueda.failed, 'falló', 'fallaron')}"
            )
        if busqueda.pruned:
            partes.append(
                f"{format_count(busqueda.pruned)} "
                f"{_plural(busqueda.pruned, 'se descartó', 'se descartaron')} antes de terminar"
            )
        probadas = _plural(total, "configuración probada", "configuraciones probadas")
        frase = f"De {format_count(total)} {probadas}, {' y '.join(partes)}."
    if busqueda.invalid_attempts:
        frase += (
            f" Las reglas de la búsqueda rechazaron {format_count(busqueda.invalid_attempts)} "
            "configuraciones seguidas, así que la búsqueda se detuvo."
        )
    return frase


def _ejemplo(causa: str, ejemplo: models.DiscardExample | None) -> str | None:
    if ejemplo is None:
        return None
    frase: str | None = None
    if causa == "ajuste_fallido":
        if ejemplo.fit is not None:
            frase = _ajuste(ejemplo.fit)
        elif ejemplo.window is not None:
            frase = "el modelo falló al ajustarse"
    elif causa == "escalon" and ejemplo.value is not None and ejemplo.threshold is not None:
        mejores = (
            "del mejor ensayo"
            if (ejemplo.keep or 1) == 1
            else f"de los {format_count(ejemplo.keep or 1)} mejores ensayos"
        )
        frase = (
            f"su error ({_numero(ejemplo.value)}) superó el umbral "
            f"{mejores} ({_numero(ejemplo.threshold)})"
        )
    elif causa == "escalon" and ejemplo.value is not None:
        frase = f"su error ({_numero(ejemplo.value)}) quedó entre los peores"
    elif causa == "poda_semantica" and ejemplo.rule:
        frase = _REGLAS_PODA.get(ejemplo.rule, "el pronóstico no servía para comparar")
        if ejemplo.value is not None:
            frase += f" (error {_numero(ejemplo.value)})"
    elif causa == "configuracion_invalida" and ejemplo.constraint:
        frase = _RESTRICCIONES.get(ejemplo.constraint, "no cumplía una regla de la búsqueda")
    if frase is None:
        return None
    lugar = _lugar(ejemplo)
    return f"Ejemplo: {lugar}{frase}."


def _lugar(ejemplo: models.DiscardExample) -> str:
    if ejemplo.rung is not None:
        ventanas = _plural(ejemplo.rung, "ventana", "ventanas")
        return f"tras {format_count(ejemplo.rung)} {ventanas} de validación, "
    if ejemplo.window is not None:
        return f"en la ventana {format_count(ejemplo.window)}, "
    return ""


def _ajuste(ajuste: models.FitFacts) -> str:
    modelo = ajuste.model or "el modelo"
    if ajuste.reason == "raiz_en_borde" and ajuste.value is not None and ajuste.limit is not None:
        return (
            f"{modelo} dejó una raíz AR/MA con módulo {_numero(ajuste.value, 4)}, por debajo del "
            f"mínimo de {_numero(ajuste.limit, 4)} (ajuste al borde de la región estable)"
        )
    return _RAZONES_AJUSTE.get(ajuste.reason, "{modelo} no pudo ajustarse").format(modelo=modelo)


def _historia(historia: models.HistoryFacts) -> str:
    return (
        f"La serie tiene {format_count(historia.available)} "
        f"{_plural(historia.available, 'observación', 'observaciones')} y se necesitan "
        f"{format_count(historia.required)}."
    )


def _frase(texto: str) -> str:
    texto = texto.strip()
    return (texto[:1].upper() + texto[1:] + ("" if texto.endswith(".") else ".")) if texto else ""


def _numero(valor: float, decimales: int = 2) -> str:
    """Un número con coma decimal y sin ceros de relleno: 1,01, 21,57, 1.234,5."""
    texto = f"{valor:,.{decimales}f}".rstrip("0").rstrip(".")
    return texto.replace(",", " ").replace(".", ",").replace(" ", ".")


def _plural(cantidad: int, singular: str, plural: str) -> str:
    return singular if cantidad == 1 else plural
