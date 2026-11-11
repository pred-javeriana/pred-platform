"""Los hechos de un error en el texto que escribía el motor antes del contrato 1.3.0.

Desde 1.3.0 el motor entrega los hechos de un error en ``ErrorInfo.facts`` (sección 11.1 del
contrato). Las corridas anteriores solo guardan el texto (``detail``), que el motor escribía con
formatos fijos:

- la búsqueda sin ensayos completos (``resumen_causas``): ``N fallidos, M podados;
  causas={...}; motivos={...}``, con los diccionarios como literales de Python, y antes de
  ``hpo.study_failed`` ``(N intentos invalidos)``;
- el motivo de cada ensayo descartado: ``ventana=4 ajuste_fallido: <error>``,
  ``escalon=4 valor=21.5 umbral_top_1=18.8``, ``ventana=10 valor=101 poda_semantica:<regla>``,
  ``ventana=3 valor=9 asha`` y ``configuracion_invalida:<módulo>.<restricción>``;
- el ajuste que falló: ``SARIMA(p, d, q)x(P, D, Q, s) con una raiz AR/MA de modulo X (< Y)``,
  ``SARIMA(...) no convergio``, ``LightGBM no ajusto`` y los tres de Chronos-2;
- la historia que faltó: ``se requieren al menos N observaciones, hay M``, ``serie de
  longitud M insuficiente: ... al menos N observaciones`` y, en una exclusión, ``historia
  admisible insuficiente: M dias, se requieren N``.

``hechos_del_texto`` traduce ese texto a la misma forma que ``ErrorFacts``, para que la interfaz
muestre igual una corrida vieja y una nueva. Lo que no reconoce lo deja fuera; nunca adivina.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Final

_TOTALES: Final = re.compile(r"(\d+) fallidos, (\d+) podados")
_INTENTOS: Final = re.compile(r"\((\d+) intentos invalidos\)")
_CAUSAS: Final = re.compile(r"causas=(\{.*?\}); motivos=")
_MOTIVOS: Final = "motivos="

_NUMERO: Final = r"(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|inf|nan)"
_AJUSTE_FALLIDO: Final = re.compile(r"^ventana=(\d+) ajuste_fallido: (.*)$", re.DOTALL)
_ESCALON: Final = re.compile(rf"^escalon=(\d+) valor={_NUMERO} umbral_top_(\d+)={_NUMERO}$")
_PODA_SEMANTICA: Final = re.compile(rf"^(?:ventana=(\d+) valor={_NUMERO} )?poda_semantica:(\w+)$")
_ASHA: Final = re.compile(rf"^ventana=(\d+) valor={_NUMERO} asha$")
_CONFIGURACION_INVALIDA: Final = re.compile(r"^configuracion_invalida:(.+)$")
_CAUSA_EN_MOTIVO: Final = re.compile(
    r"\b(configuracion_invalida|ajuste_fallido|poda_semantica|escalon)\b"
)

_MODELO_SARIMA: Final = r"(SARIMA\([\d, ]+\)x\([\d, ]+\))"
_RAIZ: Final = re.compile(
    rf"^{_MODELO_SARIMA} con una raiz AR/MA de modulo {_NUMERO} \(< {_NUMERO}\)"
)
_SIN_CONVERGENCIA: Final = re.compile(rf"^{_MODELO_SARIMA} no convergio")
_AJUSTES_FIJOS: Final = (
    (re.compile(r"^LightGBM no ajusto"), "LightGBM", "error_biblioteca"),
    (re.compile(r"^Chronos-2 no pronostico"), "Chronos-2", "pronostico_fallido"),
    (re.compile(r"^forma de cuantiles"), "Chronos-2", "forma_cuantiles"),
    (re.compile(r"^el modelo no emite el cuantil"), "Chronos-2", "cuantil_ausente"),
)
# ``NombreDeClase: `` delante del mensaje de una excepción.
_CLASE: Final = re.compile(r"^[A-Z]\w*(?:Error|Exception): ")

_HISTORIA: Final = (
    re.compile(r"se requieren al menos (?P<req>\d+) observaciones, hay (?P<disp>\d+)"),
    re.compile(
        r"serie de longitud (?P<disp>\d+) insuficiente:.*?al menos (?P<req>\d+) observaciones"
    ),
    re.compile(r"historia admisible insuficiente: (?P<disp>\d+) dias, se requieren (?P<req>\d+)"),
)


def hechos_del_texto(detalle: str | None) -> dict[str, Any] | None:
    """Los hechos (forma ``ErrorFacts``) que el texto de un error permite leer, o ``None``."""
    if not detalle:
        return None
    texto = _CLASE.sub("", detalle.strip())
    hechos: dict[str, Any] = {}
    busqueda = _busqueda(texto)
    if busqueda is not None:
        hechos["search"] = busqueda
    else:
        ajuste = _ajuste(texto)
        if ajuste is not None:
            hechos["fit"] = ajuste
        historia = _historia(texto)
        if historia is not None:
            hechos["history"] = historia
    return hechos or None


def _busqueda(texto: str) -> dict[str, Any] | None:
    totales = _TOTALES.search(texto)
    if totales is None:
        return None
    busqueda: dict[str, Any] = {
        "failed": int(totales.group(1)),
        "pruned": int(totales.group(2)),
    }
    intentos = _INTENTOS.search(texto)
    if intentos is not None and int(intentos.group(1)) > 0:
        busqueda["invalid_attempts"] = int(intentos.group(1))
    causas = _literal_dict(_CAUSAS.search(texto))
    motivos = _motivos(texto)
    busqueda["causes"] = [
        _causa(nombre, cantidad, motivos.get(nombre))
        for nombre, cantidad in sorted(causas.items(), key=lambda item: (-item[1], item[0]))
        if isinstance(nombre, str) and isinstance(cantidad, int) and cantidad > 0
    ]
    return busqueda


def _literal_dict(encontrado: re.Match[str] | None) -> dict[Any, Any]:
    if encontrado is None:
        return {}
    return _evaluar(encontrado.group(1))


def _evaluar(literal: str) -> dict[Any, Any]:
    """Un diccionario literal de Python; ``ast.literal_eval`` no ejecuta código."""
    try:
        valor = ast.literal_eval(literal)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return {}
    return valor if isinstance(valor, dict) else {}


def _motivos(texto: str) -> dict[str, str]:
    """El diccionario ``motivos={...}``: el que cierra el texto, sin el paréntesis final."""
    inicio = texto.find(_MOTIVOS)
    if inicio < 0:
        return {}
    resto = texto[inicio + len(_MOTIVOS) :].rstrip()
    # El diccionario termina en una llave; el texto puede seguir con «)» del resumen.
    for fin in range(len(resto), 0, -1):
        if resto[fin - 1] == "}":
            valor = _evaluar(resto[:fin])
            if valor:
                return {k: v for k, v in valor.items() if isinstance(v, str)}
    return {}


def _causa(nombre: str, cantidad: int, motivo: str | None) -> dict[str, Any]:
    causa: dict[str, Any] = {"cause": nombre, "count": cantidad}
    ejemplo = ejemplo_del_motivo(motivo)
    if ejemplo:
        causa["example"] = ejemplo
    return causa


def causa_del_motivo(motivo: str | None) -> str:
    """La causa de descarte de un ensayo a partir de su motivo.

    Es la regla de ``causa_trial`` del motor, con la que el motor cuenta los descartes.
    """
    if not motivo:
        return "sin_motivo_registrado"
    encontrado = _CAUSA_EN_MOTIVO.search(motivo)
    if encontrado is not None:
        return encontrado.group(1)
    if "asha" in motivo:
        return "escalon"
    return motivo.split(":", 1)[0]


def ejemplo_del_motivo(motivo: str | None) -> dict[str, Any] | None:
    """El motivo de un ensayo descartado en la forma ``DiscardExample``, o ``None``."""
    if not motivo:
        return None
    motivo = motivo.strip()
    if encontrado := _AJUSTE_FALLIDO.match(motivo):
        ejemplo: dict[str, Any] = {"window": int(encontrado.group(1))}
        ajuste = _ajuste(_CLASE.sub("", encontrado.group(2).strip()))
        if ajuste is not None:
            ejemplo["fit"] = ajuste
        return ejemplo
    if encontrado := _ESCALON.match(motivo):
        return _sin_nulos(
            {
                "rung": int(encontrado.group(1)),
                "value": _numero(encontrado.group(2)),
                "keep": int(encontrado.group(3)),
                "threshold": _numero(encontrado.group(4)),
            }
        )
    if encontrado := _PODA_SEMANTICA.match(motivo):
        ventana = encontrado.group(1)
        return _sin_nulos(
            {
                "window": int(ventana) if ventana else None,
                "value": _numero(encontrado.group(2)) if encontrado.group(2) else None,
                "rule": encontrado.group(3),
            }
        )
    if encontrado := _ASHA.match(motivo):
        return _sin_nulos(
            {"window": int(encontrado.group(1)), "value": _numero(encontrado.group(2))}
        )
    if encontrado := _CONFIGURACION_INVALIDA.match(motivo):
        restriccion = nombre_restriccion(encontrado.group(1))
        return {"constraint": restriccion} if restriccion else None
    return None


def nombre_restriccion(etiqueta: str) -> str | None:
    """El nombre corto de una restricción a partir de la etiqueta que guardaba el motor.

    La etiqueta era ``módulo.<locals>._nombre`` para una función, ``_Clase(campo=valor)`` para
    un invocable y ``lambda:<hash>`` o ``anon:<hash>`` para una restricción sin nombre (que
    queda sin nombre). Es la misma regla de ``nombre_restriccion`` del motor.
    """
    etiqueta = etiqueta.strip()
    if etiqueta.startswith(("lambda:", "anon:")):
        return None
    clase = re.match(r"^_?([A-Z]\w*)\(", etiqueta)
    if clase is not None:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", clase.group(1)).lower()
    nombre = etiqueta.rsplit(".", 1)[-1].lstrip("_")
    return nombre if re.fullmatch(r"[a-z][a-z0-9_]*", nombre) else None


def _ajuste(texto: str) -> dict[str, Any] | None:
    if encontrado := _RAIZ.match(texto):
        return _sin_nulos(
            {
                "model": encontrado.group(1),
                "reason": "raiz_en_borde",
                "value": _numero(encontrado.group(2)),
                "limit": _numero(encontrado.group(3)),
            }
        )
    if encontrado := _SIN_CONVERGENCIA.match(texto):
        return {"model": encontrado.group(1), "reason": "sin_convergencia"}
    for patron, modelo, razon in _AJUSTES_FIJOS:
        if patron.match(texto):
            return {"model": modelo, "reason": razon}
    return None


def _historia(texto: str) -> dict[str, int] | None:
    for patron in _HISTORIA:
        encontrado = patron.search(texto)
        if encontrado is not None:
            return {
                "required": int(encontrado.group("req")),
                "available": int(encontrado.group("disp")),
            }
    return None


def _numero(texto: str) -> float | None:
    """Un número del texto; ``None`` si no es finito (JSON y el contrato no lo admiten)."""
    valor = float(texto)
    return valor if valor == valor and valor not in (float("inf"), float("-inf")) else None


def _sin_nulos(hechos: dict[str, Any]) -> dict[str, Any]:
    return {clave: valor for clave, valor in hechos.items() if valor is not None}
