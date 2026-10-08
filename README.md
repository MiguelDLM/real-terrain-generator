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
5. **Conjunto Geomorfológico Completo (9 Máscaras Topográficas & Color Attribute)**:
   - Genera atributos float por máscara (o grupos de vértices, opción *Vertex Groups*) y atributos de color empaquetados (`RTG_Geo_Masks`, `RTG_Eco_Masks`) para Shaders y dispersión:
     - `RTG_Plant_Density`: Densidad vegetal filtrada estrictamente (excluye paredes y cumbres alpinas).
     - `RTG_Water_Flow`: Red hidrográfica de drenaje y escorrentía superficial acumulada (algoritmo D8).
     - `RTG_Wetness`: Índice topográfico de humedad ($\text{TWI} = \ln(A_s / \tan \beta)$) para zonas húmedas, musgos y turberas.
     - `RTG_Talus`: Acumulación de canchales y derrubios al ángulo de reposo ($30^\circ - 38^\circ$) al pie de acantilados ($>40^\circ$).
     - `RTG_Snow`: Manto nival estructural según cota de nieve, desprendimiento por gravedad ($>45^\circ$) y atrapamiento en circos glaciares cóncavos.
     - `RTG_Roughness`: Índice de rugosidad topográfica (TRI, Riley et al.) para fractura y erosión rocosa.
     - `RTG_Slope`: Pendiente del terreno de 0° (plano) a 90° (acantilado vertical).
     - `RTG_Elevation`: Altitud normalizada de 0.0 (valle) a 1.0 (cima).
     - `RTG_Valley`: Hondonadas y barrancos mediante curvatura Laplaciana.
     - `RTG_Geo_Masks`: Atributo de color Float Color (RGBA: R=Flow, G=Snow, B=Talus, A=Wetness) accesible al instante en el *Shader Editor* con un solo nodo `Input > Color Attribute`.
6. **Módulo autónomo / Zero dependencias externas**:
   - Incluye algoritmos hidrológicos y geomorfológicos puros en NumPy, decodificador de PNG RGB y codificador PNG de 16 bits sin dependencias externas pesadas.

---

## 🌲 Suite Geomorfológica y Grupos de Vértices

Al generar el terreno con la casilla **"Generate Masks"** activada, dispones de controles directos para calibrar la vegetación, escorrentía y manto nival:

* **Max Slope for Plants (°):** Ángulo máximo de inclinación para la vegetación (por defecto 35°). Las paredes más empinadas reciben 0 y son excluidas del grupo.
* **Tree Line Limit (0.0 a 1.0):** Límite de altitud para los árboles (línea alpina).
* **Valley Biomass Boost:** Incremento de biomasa vegetal en cuencas y valles protegidos.
* **Biomass Cutoff (0.0 a 0.5):** Umbral por debajo del cual la densidad vegetal pasa a 0.
* **Snow Line Altitude (0.0 a 1.0):** Cota relativa a partir de la cual se acumula nieve estructural.
* **Pack RTG_Geo_Masks (RGBA):** Crea un atributo de color nativo `FLOAT_COLOR` con 4 canales físicos para materiales procedurales en Cycles y EEVEE.

### Inspección Visual Rápida en 1 Clic
En el subpanel **Terrain Masks**, cada máscara tiene un botón de visualización (*Vegetation*, *Water Flow*, *Wetness*, *Snow*, *Scree*…) que la muestra como mapa de calor en modo *Solid*, sin cambiar de modo de edición (con *Vertex Groups* activado se usa *Weight Paint*). *Exit* restaura el sombreado del visor.

### ¿Cómo usarlos en Blender?
1. **Con Shader Editor (Materiales Procedurales):**
   - Agrega un nodo `Input > Color Attribute` y escribe `RTG_Geo_Masks`.
   - Separa con `Separate Color`:
     - **Canal R:** Escorrentía y lechos fluviales (conéctalo a rugosidad más baja o color de agua/barro).
     - **Canal G:** Nieve acumulada (conéctalo al factor de mezcla para el shader de nieve).
     - **Canal B:** Derrubios y canchales (conéctalo a textura de grava/piedras).
     - **Canal A:** Humedad topográfica TWI (conéctalo a textura de musgo o saturación).
2. **Con Geometry Nodes / Geo-Scatter:** Usa un nodo `Named Attribute` con tipo *Float*, escribe `RTG_Plant_Density` o `RTG_Water_Flow` y conéctalo al socket `Density` del nodo `Distribute Points on Faces`.
3. **Con Particle Systems:** activa *Vertex Groups* en *Terrain Masks* y, en Partículas > *Vertex Groups* > *Density*, selecciona `RTG_Plant_Density`.
4. **Edición manual en Weight Paint:** con *Vertex Groups* activado, selecciona la malla, pasa a modo *Weight Paint* (Ctrl + Tab) y retoca zonas a mano con el pincel.

---

## 🪨 Geología y Material Procedural

En el subpanel **Geology & Material**:

* **Geologic Map (Macrostrat):** descarga las unidades del mapa geológico del área ([Macrostrat](https://macrostrat.org), CC-BY 4.0), que reúne mapas de USGS, IGME, BGS y otros servicios geológicos. Escoge la escala más fina disponible: en Tenerife, el MAGNA del IGME; en los Alpes, el mapa de Europa 1:5M. Las descripciones ("Lavas and basaltic pyroclasts, trachytic and phonolytic") se traducen a tipos de roca.
* **Erosión diferencial:** dentro de cada unidad, las rocas duras (basalto, caliza, cuarcita) ocupan escarpes y terreno abrupto, y las blandas (piroclastos, margas, lutitas) las pendientes suaves.
* **Bloques sueltos** (`RTG_Boulder_Density`, `RTG_Clast_Size_m`, `RTG_Clast_Rounding`): canchales angulosos al pie de los cantiles, cantos rodados en los cauces (de mayor tamaño cuanto más fuerte es la corriente) y bloques en afloramientos duros.
* **Material "RTG Terrain Surface"** (EEVEE y Cycles): relieve de roca según su meteorización (juntas en bloques, disyunción columnar, estratos, bolos, vesículas, karst), con canchal, suelo derivado de la roca madre, vegetación, cauces húmedos y nieve encima. Las texturas usan metros reales (`RTG_Position_m`), así que no se estiran en los cantiles ni dependen de la escala de la escena. Relief: *Bump* (rápido) o *Displacement* (desplazamiento real con subdivisión adaptativa). Quality: *Preview* (menos octavas, viewport fluido) o *Final* (para render). Cada terreno usa una variante del shader con solo los términos que necesitan sus rocas (una llanura de una sola litología evalúa uno o dos; un cañón calizo, tres), más paquetes de estratos de decenas de metros y chorreras verticales en paredes empinadas.

Atributos de malla disponibles para Shader y Geometry Nodes: `RTG_Rock_Color`, `RTG_Rock_Color_Alt`, `RTG_Rock_Fracture` (bloques, columnas, estratos, bolos), `RTG_Rock_Surface` (vesículas, karst, grano, rugosidad), `RTG_Rock_Joint_m`, `RTG_Rock_Bedding_m`, `RTG_Strata_Depth_m`, `RTG_Hardness`, `RTG_Rock_ID`, `RTG_Elevation_m`, `RTG_Eco_Masks` (R vegetación, G pendiente, B vaguada, A rugosidad).

**Referencias:** Peters, Husson & Czaplewski (2018) *Macrostrat*, G³, doi:10.1029/2018GC007467 · Hartmann & Moosdorf (2012) *GLiM*, G³, doi:10.1029/2012GC004370 · Galin et al. (2019) *A Review of Digital Terrain Modeling*, CGF, doi:10.1111/cgf.13657 · Townsend, Gallen & Clark (2020) JGR Earth Surface, doi:10.1029/2020JF005665 · Selby (1980) *Z. Geomorph.* 24.

---

## 🌅 Escena y Render Realista

En el subpanel **Scene & Render**, **Setup Realistic Scene** prepara la escena alrededor del terreno seleccionado:

* **Cielo físico** (mundo `RTG Sky`, *Multiple Scattering* en Blender 5.0+, Nishita antes): su disco solar ilumina la escena, así que las lámparas Sun existentes se ocultan para no duplicar la luz. El mundo anterior se conserva. El sol se coloca por **elevación** y **azimut de brújula** (0 norte, 90 este, igual que los ejes +Y norte / +X este del terreno), o con **Sun from Time** a partir de la latitud, el día del año y la hora solar. *Sky Haze* controla el polvo y la humedad del cielo.
* **Perspectiva aérea**: caja de bruma (`RTG_Atmosphere_*`) cuya densidad sale de la **visibilidad** en km (ley de Koschmieder, extinción = 3.912 / visibilidad) convertida a unidades de escena: las crestas lejanas se aclaran y azulan a la distancia real, sea cual sea la escala. Trópico húmedo 10–20 km, aire limpio 50–100 km.
* **Cámara** (`RTG_Camera`): vista aérea oblicua desde un extremo del terreno, a lo largo de su eje largo y con el sol de lado (la luz lateral muestra el relieve; la frontal lo aplana). Sin profundidad de campo: con un terreno a escala reducida, el desenfoque físico da aspecto de maqueta.
* **Render**: *Preview* (128 muestras) o *Final* (512 muestras con muestreo adaptativo y denoise, material en calidad Final), y AgX *Medium High Contrast*.

## 🔗 Integración con Procedural Plant Generator

Real Terrain Generator prepara el terreno (relieve, geología, rocas, agua) y Procedural Plant Generator pone las plantas. El reparto de la PPG Forest lee la geometría **evaluada** del terreno (*Object Info*), así que todo lo que añade el modificador *Cliff Detail* (contrafuertes, repisas) recibe plantas con la orientación correcta.

**Contrato de datos (versión 1)**, estable para otras extensiones. En el objeto terreno:

| Propiedad / atributo | Tipo | Significado |
|---|---|---|
| `rtg_is_terrain` | bool | Objeto generado por RTG |
| `rtg_contract_version` | int | Versión de este contrato |
| `rtg_bu_per_m` | float | Unidades de escena por metro real (1.0 en escala *Real*) |
| `RTG_Plant_Density` | float, punto | Densidad vegetal 0–1 (incluye repisas de pared) |
| `RTG_Ledge` | float, punto | Repisas en paredes (geometría evaluada) |
| `RTG_Wetness`, `RTG_Water_Flow`, `RTG_Slope`, `RTG_Snow`, `RTG_Valley` | float, punto | Máscaras ambientales 0–1 |
| `RTG_Elevation_m`, `RTG_Position_m` | float / vector, punto | Altitud y posición reales en metros |
| `RTG_Hardness`, `RTG_Rock_ID` | float / int, punto | Roca bajo cada punto |
| `RTG_Boulder_Density`, `RTG_Clast_Size_m` | float, punto | Dónde hay bloques sueltos y su tamaño (útil para que las plantas los eviten) |
| hijos `rtg_is_rocks` / `rtg_is_water` | objetos | Rocas y agua del terreno |

Uso en Geometry Nodes: `Named Attribute ("RTG_Plant_Density")` → *Density Factor* de *Distribute Points on Faces*. Las máscaras son atributos por defecto (o grupos de vértices con la opción *Vertex Groups*); *Named Attribute* lee ambos.

**Escala:** densidades en árboles/ha y separaciones en metros suponen 1 unidad = 1 m. Usa la escala **Real** para escenas con plantas, o convierte con `rtg_bu_per_m`.

### Agua (ríos, lagos, presas)
*Water Surface* crea un objeto agua hijo del terreno. Dos modos:
- **Lake**: superficie plana al *Water Level* (lagos, embalses, mar). El cuentagotas fija el nivel justo por encima del terreno más bajo, porque SRTM y otros DEM registran la superficie del agua.
- **River**: ríos a lo largo de la red de drenaje, con pendiente, corriente, rápidos y espuma. El *Water Level* sigue llenando lagos por debajo de él (bájalo bajo el terreno para tener solo ríos).

La malla se construye a partir de la propia malla del terreno, solo donde hay agua, con un margen que queda oculto bajo la orilla.

**Hidráulica del modo River** (`core/river.py`):
- Red de drenaje D8 sobre el DEM con las depresiones rellenadas (*Priority-Flood*, Barnes et al. 2014): el flujo atraviesa los hoyos del DEM y las superficies planas de los embalses.
- *Through Flow*: el río principal entra por un borde y sale por el punto más bajo del borde, con todo su caudal (la cuenca de un río suele quedar fuera del área). Se traza por el fondo del valle, también a través de embalses planos.
- Ancho por geometría hidráulica (w = 3 Q^0.5, Leopold & Maddock 1953); calado y velocidad por **Manning** (*Bed Roughness* = n), con la pendiente limitada en cascadas, donde Manning deja de valer (Rickenmann 2001).
- **Número de Froude** Fr = v/√(g·d): por encima de 1 (régimen supercrítico) aparecen aguas blancas. Un torrente de montaña espumea solo; un río de llanura queda liso. La espuma viaja aguas abajo y se diluye en las confluencias según el caudal.
- Un río ahogado por un lago se frena (mismo caudal por una sección más profunda) y pierde sus rápidos.

Atributos del agua: `RTG_Water_Depth`, `RTG_Water_Velocity` (m/s, x este, y norte), `RTG_Water_Flow_Coords` (s a lo largo de la corriente, n a lo ancho, peso del río), `RTG_Water_Whitewater`, `RTG_Water_Foam`.

Material "RTG Water Surface" (EEVEE y Cycles):
- color y transparencia según la profundidad (absorción de Beer‑Lambert, control *Clarity*);
- **flow map de dos fases** (Vlachos 2010): ondas y vetas de espuma texturizadas en coordenadas de corriente (s, n), así que siguen las curvas del río, y arrastradas por la velocidad local sin estirarse nunca;
- **animación en bucle perfecto**: el periodo divide el rango de frames de la escena, y la evolución del patrón recorre un círculo en ruido 4D una vez por bucle. Unos drivers leen el rango de frames y los fps, así que el bucle se adapta si cambian;
- aguas blancas en rápidos, vetas de espuma aguas abajo, remolinos que alisan o rizan parches de la corriente, ráfagas de viento (*Wind Gusts*) y espuma en la orilla;
- las ondas más pequeñas que un píxel siguen difuminando el reflejo (la rugosidad sigue a la altura de las ondas).

En el terreno, el material añade una franja mojada sobre la línea del agua y un tono de limo bajo ella.

**Volumetric Water** (para Cycles):
- La malla del agua se cierra bajo el terreno y se llena con un *Principled Volume*. Su densidad está en metros reales (1 / *Clarity*), así que la absorción y la dispersión verdosa del limo dependen de la profundidad real; el modo normal, más ligero y pensado para EEVEE, aproxima esto con un atributo de profundidad.
- En ambos modos, los rayos de sombra atraviesan la superficie (*Light Path → Is Shadow Ray*). El fondo queda iluminado sin ruido de cáusticas y el render converge antes.
- *Ripple Sharpness* afila las crestas de las ondas.

### Rocas sueltas (Scatter Rocks)
*Scatter Rocks* crea un objeto `RTG_Rocks_<terreno>` (hijo del terreno) con el modificador Geometry Nodes *RTG Rock Scatter*. Lee el terreno **evaluado**, así que las rocas se apoyan en las paredes esculpidas por *Cliff Detail*.

- **Dónde:** según `RTG_Boulder_Density`, es decir, canchales al pie de los cantiles, cauces y afloramientos duros.
- **Tamaño:** `RTG_Clast_Size_m`, en metros reales.
- **Forma:** bloques angulosos, cantos rodados (cauces y granitos), lajas (rocas en capas) o prismas (basaltos), según la redondez y la fractura de la roca madre.
- **Rendimiento:** *Viewport Display* (fracción visible en el viewport) y *Camera Range (m)* (solo alrededor de la cámara), para planos a ras de suelo.

La biblioteca *RTG Rock Library* (4 formas × 4 variantes, 1 m) se puede usar en cualquier otro árbol de Geometry Nodes.

El material *RTG_Rock* hereda el color de la roca del terreno en cada punto, pone musgo en las caras superiores y una franja mojada en la línea del agua.

### Cliff Detail (paredes de cañón)
Un DEM de 30 m deja muy pocas filas de vértices en una pared vertical, que se ve como una lámina lisa. El modificador *RTG Terrain Detail* subdivide solo las caras empinadas (1 nivel en el viewport, 3 en el render) y las desplaza horizontalmente, en metros reales, con:
- contrafuertes y acanaladuras;
- repisas escalonadas, donde crece la vegetación;
- bloques diaclasados.

Genera `RTG_Wall_Cavity` (el material lo usa para oscurecer huecos y poner musgo) y `RTG_Ledge`.

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
