# Real Terrain Generator (Blender Extension)

**Real Terrain Generator (RTG)** es una extensión para Blender 4.2+ que genera mallas 3D de terrenos del mundo real y mapas de desplazamiento de alta precisión (**16-bit Grayscale Heightmaps**) consultando bases de datos globales de modelos de elevación digital (**DEM**).

Diseñada especialmente como el complemento geográfico ideal para **Procedural Plant Generator**: al generar el terreno, se calculan automáticamente máscaras topográficas (altitud, pendiente y densidad biológica) para guiar dónde crecen las plantas, árboles y vegetación.

---

## 🌟 Características Principales

1. **Sin fricción (Zero API Keys)**:
   - Implementa **Opción A (AWS Terrain Tiles - Terrarium)** por defecto.
   - Acceso inmediato al bucket público de AWS S3 Open Data sin necesidad de registrarse ni configurar tokens.
2. **Buscador de lugares y accidentes geográficos**:
   - Geocodificación integrada con **OpenStreetMap Nominatim**.
   - Escribe simplemente *"Monte Everest"*, *"Teide"*, *"Grand Canyon"* o *"Fuji"* y el sistema obtendrá automáticamente las coordenadas y sugerirá el área y nivel de zoom óptimo.
3. **Escala y Detalle métrico real**:
   - Selección de área en kilómetros ($X \times Y$).
   - Control de resolución mediante niveles de zoom ($z=9$ a $z=15$, alcanzando hasta ~4 a 9 metros por píxel).
   - Indicador en tiempo real de la resolución en el terreno (`~19 m / píxel`).
4. **Sin artefactos de escalonado (16-bit Grayscale & 32-bit Float)**:
   - Evita el típico escalonado (*terracing*) de los mapas de 8 bits estándar (256 niveles).
   - Normalización a **16 bits (65.536 niveles de gris)** y almacenamiento opcional de archivos PNG de 16 bits.
5. **Máscaras botánicas para Procedural Plant Generator**:
   - Genera grupos de vértices (*Vertex Groups*) en la malla:
     - `RTG_Elevation`: Normalizado de 0 (valle) a 1 (cima). Permite estratificar biomas (ej. coníferas en cotas altas, caducifolios en cotas medias).
     - `RTG_Slope`: Pendiente del terreno de 0° (plano) a 60°+ (acantilado vertical). Evita que se siembren árboles en paredes rocosas verticales.
     - `RTG_Plant_Density`: Máscara combinada de suelo fértil (suelo llano + valles protegidos).
     - `RTG_Valley`: Detección de hondonadas y barrancos mediante curvatura topográfica (Laplaciano).
6. **Módulo autónomo / Zero dependencias externas**:
   - Incluye un decodificador de PNG RGB y codificador de PNG 16 bits puro en Python + NumPy (sin necesidad obligatoria de Pillow ni OpenCV).

---

## 🚀 Instalación en Blender 4.2+

### Como Extensión / Addon:
1. Clona o copia la carpeta `real-terrain-generator` dentro de tu directorio de extensiones o addons de Blender.
2. En Blender 4.2+: ve a **Edit > Preferences > Add-ons (o Get Extensions)** y activa **Real Terrain Generator**.
3. En la vista 3D (*3D Viewport*), pulsa la tecla `N` para abrir la barra lateral y selecciona la pestaña **Real Terrain**.

---

## 🗺️ Opciones de DEM: Estado Actual y Hoja de Ruta

### ✅ Opción A: AWS Terrain Tiles (Terrarium) — *IMPLEMENTADA Y ACTIVA*
* **Endpoint:** `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`
* **Autenticación:** Ninguna (Bucket público en AWS S3).
* **Fórmula de decodificación:**
  $$\text{altura\_metros} = (R \times 256 + G + B / 256) - 32768$$
* **Cobertura:** Global (mosaico compilado por Mapzen/Nextzen a partir de USGS 3DEP, SRTM y ETOPO1).
* **Resolución:**
  * Zoom 9–11: Escala regional/macizo montañoso (~75 a 150 m/px).
  * Zoom 12–13: Montañas, valles y cordilleras detalladas (~19 a 38 m/px).
  * Zoom 14–15: Máximo detalle (~4.8 a 9.5 m/px en zonas con cobertura USGS/LiDAR).

---

### ✅ Opción B: OpenTopography Global DEM API — *IMPLEMENTADA Y ACTIVA*
* **Endpoint:** `https://portal.opentopography.org/API/globaldem`
* **Autenticación:** Requiere API Key gratuita registrada en [portal.opentopography.org](https://portal.opentopography.org/myopentopo).
* **Configuración de la API Key:**
  * **Desde el menú de Ajustes:** `Edit > Preferences > Add-ons > Real Terrain Generator > OpenTopography API Key`.
  * **O directamente desde el panel 3D:** En la sección *DEM Provider*, al seleccionar OpenTopography aparece el botón *"Settings Menu"* y un campo para introducir o sobrescribir la clave en la sesión.
* **Formatos:** Descarga en formato **AAIGrid (Arc ASCII Grid)** nativo, garantizando compatibilidad multiplataforma sin depender de librerías externas de C (como GDAL).
* **Catálogos soportados:**
  * `COP30` (Copernicus DEM 30m de la ESA): El modelo global más preciso y moderno (por defecto).
  * `AW3D30` (ALOS World 3D de JAXA, 30m).
  * `SRTMGL1` (SRTM 30m de NASA).
  * `SRTMGL3` (SRTM 90m de NASA).
  * `NASADEM` (NASADEM Global 30m).

---

### 📝 Notas de Arquitectura para Futuras Opciones

El código está estructurado con una clase base abstracta `DEMProvider` en [`core/dem_sources/base.py`](file:///home/miguel/Documents/software/real-terrain-generator/core/dem_sources/base.py), lo que permite incorporar fácilmente los siguientes proveedores:

#### 2. Opción C: Mapbox Terrain-RGB ([`core/dem_sources/mapbox_rgb.py`](file:///home/miguel/Documents/software/real-terrain-generator/core/dem_sources/mapbox_rgb.py))
* **Ventajas:** Cobertura global homogénea y pre-filtrada en proyección Web Mercator con precisión vertical de 0.1 metros (10 cm).
* **Fórmula de decodificación:**
  $$\text{altura\_metros} = -10000 + ((R \times 256 \times 256 + G \times 256 + B) \times 0.1)$$
* **Requisitos:** Requiere un `access_token` de Mapbox (cuenta gratuita con hasta 200.000 solicitudes de teselas mensuales).

#### 3. Opción D: USGS 3DEP ([`core/dem_sources/usgs_3dep.py`](file:///home/miguel/Documents/software/real-terrain-generator/core/dem_sources/usgs_3dep.py))
* **Ventajas:** Modelos de elevación bare-earth obtenidos por LiDAR aéreo con resoluciones de **1 metro y 3 metros**.
* **Limitación:** Exclusivo de Estados Unidos y territorios asociados.
* **Endpoint:** WCS ImageServer de The National Map (`elevation.nationalmap.gov`).
* **Nota:** Gran parte del catálogo 3DEP de 10m y áreas seleccionadas de 1m ya están asimiladas en las teselas de AWS a niveles de zoom 14–15.

---

## 🌲 Integración con Procedural Plant Generator

Cuando generas un terreno con la casilla **"Generate Botanical Masks"** activada, el objeto resultante contendrá grupos de vértices listos para ser usados como máscaras de distribución de plantas o modificadores de dispersión (*Geometry Nodes / Particle Systems*):

```
+-----------------------------------------------------------+
|                     Terreno 3D Real                       |
|                                                           |
|  [Cima rocosa / Nieve]  --> RTG_Elevation ~ 1.0           |
|                             RTG_Slope > 0.8 (Pared)       |
|                             -> Sin árboles / Rocas        |
|                                                           |
|  [Laderas medias]       --> RTG_Elevation ~ 0.5           |
|                             RTG_Slope ~ 0.2 - 0.5         |
|                             -> Coníferas (Pinos / Abetos) |
|                                                           |
|  [Valle / Suelo llano]  --> RTG_Elevation ~ 0.1           |
|                             RTG_Plant_Density ~ 1.0       |
|                             -> Bosque denso / Arbustos    |
+-----------------------------------------------------------+
```

---

## 🧪 Pruebas Unitarias

El motor central está completamente desacoplado de la API interna de Blender, permitiendo ejecutar pruebas unitarias con `pytest`:

```bash
pytest -v
```

Las pruebas cubren:
- Conversión de coordenadas WGS84 a teselas Slippy Map y viceversa.
- Decodificación y fórmula matemática de elevación de teselas Terrarium (altitud positiva, nivel del mar y depresiones negativas).
- Codificación y escritura de imágenes PNG en escala de grises de 16 bits sin dependencias externas.
- Cálculo de pendientes (*Slope*), orientación (*Aspect*) y curvatura topográfica (*Curvature*).
- Ensamblado y subdivisión de mallas con UVs y grupos de vértices.
- Consulta de geocodificación mediante OpenStreetMap Nominatim.
