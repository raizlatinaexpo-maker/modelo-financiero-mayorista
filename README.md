# Raíz Latina Mayorista

## Instalación
1. Instala Python 3.11 o superior.
2. Abre una terminal en esta carpeta.
3. Ejecuta:
   pip install -r requirements.txt
4. Ejecuta:
   streamlit run app.py

La aplicación crea automáticamente `raiz_latina.db`.

## Primera configuración
1. Entra a Configuración.
2. Define las tasas comerciales EUR/COP y USD/COP.
3. Define el margen objetivo de productos (25% por defecto).
4. Crea clientes.
5. Registra pedidos.
6. Registra pagos.
7. Completa el costo real del envío cuando llegue la factura de DHL/FedEx/etc.
8. Usa Reportes para exportar Excel.

## Nota importante
La tasa automática usa Frankfurter como referencia. Las tasas guardadas en cada pedido/pago/gasto no se modifican automáticamente. Para un sistema contable definitivo conviene validar posteriormente la fuente de tasa que Raíz Latina quiera adoptar oficialmente y la política contable aplicable.


## V2
- Buscador de clientes dentro del selector.
- Fuente automática de tasas con fallback.
- Utilidad separada entre productos y envío real.
- Estados logísticos: POR CONFIRMAR, MERCANCÍA SOLICITADA, LISTO PARA ENVÍO y ENVIADO.
- Estados visuales y edición desde Envíos pendientes.

- Clientes con dirección, ciudad y código postal.
- Ficha editable del cliente con total comprado y número de pedidos.
- Eliminación protegida: no se permite borrar clientes que tengan pedidos asociados para conservar el historial.
