def classFactory(iface):
    from .plugin import GeorefAutosavePlugin
    return GeorefAutosavePlugin(iface)
