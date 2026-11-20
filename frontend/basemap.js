// Base map tiles for the Leaflet maps (live map and the staff/admin complaint detail map).
//
// OpenStreetMap's volunteer tile servers require a Referer header and answer "403 Access blocked" without one.
// Browsers send no Referer for pages opened straight from disk (file://, e.g. via start_demo.bat), so in that
// case the pages use Esri's World Street Map tiles instead, which don't need one. Pages served over http(s)
// (Nginx in Docker, or any local web server) keep the OpenStreetMap tiles.
window.SmartCityBasemap = function (options) {
  const fromDisk = window.location.protocol === 'file:';
  const common = Object.assign({ maxZoom: 19 }, options || {});
  if (fromDisk) {
    return L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
      Object.assign(common, {
        attribution: 'Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Esri, HERE, Garmin, ' +
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      })
    );
  }
  return L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', Object.assign(common, {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }));
};
