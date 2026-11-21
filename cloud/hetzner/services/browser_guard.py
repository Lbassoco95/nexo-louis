#!/usr/bin/env python3
"""browser_guard.py — candados del navegador de Donna: NUNCA paga ni teclea datos de tarjeta.

Paso 1 de «compras»: Donna busca, compara y deja la compra lista (enlace al comercio); el pago lo hace Polo con Apple Pay en su teléfono.
Este módulo lo hace cumplir por CÓDIGO (no solo por instrucciones del modelo, que una página web podría intentar engañar):
  • no se teclea nada en campos de pago (tarjeta, CVV, vencimiento, CLABE, NIP, códigos SMS…);
  • no se hace clic en botones que completan un pago o una compra («Pagar», «Confirmar compra», Apple Pay…).
Las funciones son puras (reciben texto/atributos) para poder probarlas sin navegador."""
import re

MENSAJE = ("🛑 Por seguridad Donna no hace pagos ni teclea datos de tarjeta. Deja la compra lista (comercio, función, asientos, total) y entrégale a Polo "
           "el enlace con [[pagar:URL|descripción]] para que pague él con Apple Pay.")

# Botones/enlaces que COMPLETAN un pago. «Comprar boletos» (que solo abre la selección de asientos) NO está aquí a propósito.
_CLIC_PAGO = re.compile(
    r"(?i)(\bpagar\b|\bpago\s+(seguro|con|ahora)\b|realizar\s+(el\s+)?pago|confirmar\s+(la\s+|mi\s+)?(compra|pago|orden|pedido)|finalizar\s+(la\s+|mi\s+)?(compra|pago)|"
    r"completar\s+(la\s+|mi\s+)?(compra|pago)|comprar\s+ahora|\bplace\s+order\b|\bpay\s+now\b|\bpay\b\s*(with|\$)|apple\s*pay|google\s*pay|paypal|"
    r"enviar\s+pago|autorizar\s+(el\s+)?pago)")

# Campos de pago o de verificación bancaria: nombre/id/autocomplete/placeholder/aria-label/etiqueta.
_CAMPO_PAGO = re.compile(
    r"(?i)(\bcc-|card[-_ ]?(number|num|no|holder|name)?\b|tarjeta|\bcvv\b|\bcvc\b|\bcsc\b|security[-_ ]?code|c[oó]digo\s+de\s+seguridad|expir|vencimiento|"
    r"\bclabe\b|\biban\b|n[uú]mero\s+de\s+cuenta|cuenta\s+bancaria|\bnip\b|\bpin\b|\botp\b|c[oó]digo\s+(sms|de\s+verificaci[oó]n\s+bancario)|3-?d\s*secure)")


def bloquea_clic(*textos: str) -> bool:
    """True si el texto visible / aria-label / value del elemento corresponde a completar un pago."""
    t = " ".join(str(x or "") for x in textos)
    return bool(_CLIC_PAGO.search(t))


def bloquea_campo(**attrs) -> bool:
    """True si el campo es de pago. attrs: name, id, autocomplete, placeholder, aria_label, label, type, selector…"""
    if str(attrs.get("autocomplete", "")).lower().startswith("cc-"):
        return True
    t = " ".join(str(v or "") for v in attrs.values())
    return bool(_CAMPO_PAGO.search(t))
