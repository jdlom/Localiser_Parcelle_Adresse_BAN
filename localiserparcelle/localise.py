#!/usr/bin/env python
# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import (QCoreApplication, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, QSettings, Qt, QUrl, QTranslator, pyqtProperty)
from qgis.PyQt.QtGui import (QColor, QIcon, QPainter)
from qgis.PyQt.QtWidgets import (QApplication, QAction, QMessageBox, QSizePolicy)
from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsNetworkAccessManager, QgsPointXY, QgsProject, QgsRectangle)
from qgis.gui import (QgsMapCanvasItem, QgsVertexMarker)

from io import StringIO
from os.path import (dirname, join, exists)

from localiserparcelle.ui_control import ui_control
from .http_finder import CartelieFinder
from .ban_locator_filter import BanLocatorFilter


class plugin(QObject):

	def __init__(self, iface):
		QObject.__init__(self)
		self.iface = iface
		# translation environment
		self.plugin_dir = dirname(__file__)
		#locale = QSettings().value("locale/userLocale")[0:2]
		locale = 'fr'
		localePath = join(self.plugin_dir, 'i18n', 'localiseparcelle_{0}.qm'.format(locale))
		if exists(localePath):
			self.translator = QTranslator()
			self.translator.load(localePath)
			QCoreApplication.installTranslator(self.translator)

		#locator
		self.locator_filter = BanLocatorFilter(self)
		self.iface.registerLocatorFilter(self.locator_filter)

	def initGui(self):
		self.settings = 'Plugin-LocaliserParcelleAdresse/' # Pour mémoriser les parametres de recherche
		self.manager = QgsNetworkAccessManager.instance()
		self.tmpGeometry = []
		self.lstListes = [] # Les listes déroulantes de l'écran : region, dep, comm...
		icon = join(dirname(__file__), "icone.png")
		self.action = QAction(QIcon(icon), "Localiser Parcelle Adresse (Ban)", self.iface.mainWindow())
		self.iface.mainWindow().setAttribute(Qt.WA_DeleteOnClose)
		self.action.setWhatsThis("Aller de la region a la parcelle ou Adresse")
		self.action.setStatusTip("Aller de la region a la parcelle ou Adresse")
		self.action.triggered.connect(self.run)
		self.iface.addToolBarIcon(self.action)
		self.iface.addPluginToMenu("&Localiser Parcelle ou Adresse (Ban)", self.action)

		self.barInfo = self.iface.messageBar() 
		self.barInfo.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
		self.dlg, self.marker = None, None
		self.color = QColor(0, 255, 0, 125)

		self.dlg = ui_control(None, Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint )
		# fermeture de la fenetre du plugin on deroute sur une fonction interne
		self.dlg.closeEvent = self.close_Event
		########################
		#INSERTION DES SIGNAUX #
		########################
		self.dlg.bInfo.clicked.connect(self.getAbout)
		#self.dlg.lastWindowClosed.connect(self.closeDlg)
		self.dlg.bQuit.clicked.connect(self.closeDlg)
		self.dlg.bErase.clicked.connect(self.cleanSearch)
		self.dlg.lRegion.activated[int].connect(self.getListDepartements)
		self.dlg.lDepartement.activated[int].connect(self.getListCommunes)
		self.dlg.lCommune.activated[int].connect(self.getListSections)
		self.dlg.lCommune.editTextChanged.connect(self.getListSectionsByText)
		self.dlg.lCommune.activated[int].connect(self.updateCodecity)
		#self.dlg.lCommune.currentIndexChanged.connect(self.updateCodecity)
		self.dlg.lSection.activated[int].connect(self.getListParcelles)
		self.dlg.bZoom.clicked.connect(self.getLocation)
		self.dlg.adrin.returnPressed.connect(self.getLocationByAdress)
		self.dlg.colorMarker.setColor(self.color)
		self.dlg.colorMarker.colorChanged.connect(self.setColor)
		self.dlg.dynaMarker.clicked.connect(self.setMarker)
		self.dlg.scale.valueChanged.connect(self.setScale)

		self.dlg.opacityMarker.setOpacity(float((self.color.alpha()/255))) 
		self.dlg.opacityMarker.opacityChanged.connect(self.setColor)
		

	def unload(self):
		self.iface.removePluginMenu("&Localiser Parcelle ou Adresse (Ban)",self.action)
		self.iface.removeToolBarIcon(self.action)
		self.iface.deregisterLocatorFilter(self.locator_filter)
		self.locator_filter = None
		
	def close_Event(self, event): self.cleanMarker()

	def run(self):
		if self.lstListes: # Si l'écran a deja ete lancé dans cette session
			self.dlg.show()
			return


		self.dlg.show()
		##############################
		# VARIABLES D'INITIALISATION #
		##############################
		self.lstListes = [self.dlg.lRegion, self.dlg.lDepartement, self.dlg.lCommune, self.dlg.lSection, self.dlg.lParcelle]
		self.lstListes_label = ["-- REGION --", "-- DEPARTEMENT --", "-- COMMUNE --", "-- SECTION --", "-- PARCELLE --"]
		self.results = [1,2,3,4,5]
		
		self.getListAction(0)
		
		s = QSettings() # QGIS options settings
		scale = s.value("%szoom" % self.settings, "" )
		if scale == "" : scale = "100"
		self.dlg.scale.setValue(int(scale))
		
		region = s.value("%sregion" % self.settings, "" )
		if region == "": return
		region = int(region)
		if region > self.dlg.lRegion.count()-2: return # Si le nb de regions a changé !
		self.dlg.lRegion.setCurrentIndex( region+1 )
		
		dep = s.value("%sdepartement" % self.settings, "" ) # Il faut le faire avant getListAction(1)
		self.getListAction(1)
		if dep == "" : return
		dep = int(dep)
		if dep > self.lstListes[1].count()-2: return # Si le nb de dep a changé !
		self.lstListes[1].setCurrentIndex( dep+1 )
		
		self.getListAction(2)
	
	def cleanSearch(self):
		self.dlg.commune_adresse_disable('')
		for j in range(3, 5): self.lstListes[j].clear(); self.lstListes[j].addItem(self.lstListes_label[j])
		self.lstListes[2].setCurrentIndex(0)
		self.cleanMarker()

	def cleanMarker(self):
		# detruire les precedents marqueurs
		for i in range(0,len(self.tmpGeometry)): self.iface.mapCanvas().scene().removeItem(self.tmpGeometry[i])
		self.marker = None
		self.tempGeometry = []

	def closeDlg(self): self.dlg.reject(); self.cleanMarker(); 

	def getListDepartements(self, index): self.getListAction(1)
	def getListCommunes(self, index): self.getListAction(2)
	def getListSectionsByText(self, text): 
		text = self.dlg.lCommune.currentText()
		index = self.dlg.lCommune.findText("%s" % (text))
		# try : print('getListSection'); print(self.results[3]); print('-----')#[index]["code"])
		# except : pass
		try :
				if index > -1 and index < (self.dlg.lCommune.maxIndex-1) : self.dlg.lCommune.setCurrentIndex(index); self.getListAction(3)
		except : pass
		
	def getListSections(self, index): self.getListAction(3)
	def getListParcelles(self, index): self.getListAction(4)
	
	#######################################	
	# REINITIALISATION DES LISTES D'APRES #
	#######################################
	def getListAction(self, indexListe):
		s = QSettings() 
		for j in range(indexListe, 5):
			self.lstListes[j].clear(); self.lstListes[j].addItem(self.lstListes_label[j])

		#############################################
		# RECUPERATION des DONNEES SERVICE CARTELIE #
		#############################################
		if indexListe > 0:
			index = self.lstListes[indexListe-1].currentIndex()-1
			if index < 0 or index >= len(self.results[indexListe-1]) : return
			code = self.results[indexListe-1][index]["code"]
			cartelie_finder = CartelieFinder(indexListe, code, parent=self.dlg)
		else:
			cartelie_finder = CartelieFinder(indexListe, parent=self.dlg)

		result = cartelie_finder.get_data()
		if not result:
				QMessageBox.warning( self.dlg, u"Erreur réseau", u'Serveur de localisation (Cartélie) injoignable :'
				+u"\n 1. Vérifier vos paramètres réseau:\n   Préférences > Options > Réseau > Proxy\n 2. Réessayer plus tard ..." )
				return

		self.results[indexListe] = result
		###################################
		# REMPLISSAGE DE LA LISTE D'APRES #
		###################################
		n = len(result)
		for i in range(n):
			if indexListe == 2 :
				libelle = "%s - %s" % (result[i]["code"], result[i]["nom"])
			else :
				libelle = "%s (%s)" % (result[i]["nom"], result[i]["code"]) if indexListe == 1 else result[i]["nom"]
			self.lstListes[indexListe].addItem(libelle)
		try : self.lstListes[indexListe].getMaxIndex()
		except : pass
		#######################################
		# Enregistrer parametres de recherche #
		#######################################
		# enregistre le choix de la région ou du dép.
		if   indexListe == 0 : self.dlg.commune_adresse_disable('')
		elif indexListe == 1 : self.dlg.commune_adresse_disable(''); s.setValue("%sregion" % self.settings, index ); s.setValue("%sdepartement" % self.settings, "" )
		elif indexListe == 2 : self.dlg.commune_adresse_disable(''); s.setValue("%sdepartement" % self.settings, index )
		elif indexListe == 3 : self.dlg.commune_adresse_enable(self.dlg.lCommune.currentText())
		## /Enregistrer parametres de recherche

	def getLocation(self):
		# Si tab Parcelle activer recherche parcelle
		self.getLocationByParcelId() if self.dlg.infracommune.currentIndex() == 0 else self.getLocationByAdress()

	def getLocationByAdress(self):
		#http://api-adresse.data.gouv.fr/search/?q=rue&citycode=2A004&limit=5000
		self.dlg.adrout.clear()
		result = self.dlg.adrin.get_search_result()

		if not result:
				self.dlg.affiche_adresse('<html><body><p align=left color=red><b>Adresse non trouvée</p></b></body></html>')
				return

		adresse, score, typeInfo, x, y = result
		self.dlg.affiche_adresse('<html><body><p align=left>Trouvé (%s):<br>%s<br>correspondance=%s &#37;</p></body></html>' % (typeInfo, adresse, score))
		# Transformer les coordonnees du 4326 du Gécodeur Etalab -> Systeme projection projet
		# recuperation CRS projet
		transformer = self.getTransformer(4326)
		point = transformer.transform( QgsPointXY(x,y), QgsCoordinateTransform.ForwardTransform)
		x, y = point[0], point[1]


		# Zoomer sur le point
		self.zoomTo(x, y, x, y)
	
	def getLocationByParcelId(self):
		###################################################
		# RECUPERATION DE LA LISTE ACTIVE ET DE SON INDEX #
		###################################################
		indexListe = None
		for i in range(4,-1,-1): 
			if self.lstListes[i].currentIndex() > 0 :
				# RECUPERATION DE L'ID DE LA LISTE
				indexListe = i
				break
		if indexListe == None:
			QMessageBox.information(self.iface.mainWindow(), "Zoom impossible",unicode("Pas si vite! Vous n'avez même pas choisi de région où aller !",'UTF-8'))
		else:
			index_lActuelle = self.lstListes[indexListe].currentIndex()-1
		
		result = self.results[indexListe][index_lActuelle]

		if not result :
			QMessageBox.warning(self.dlg,"Message :", '(Erreur Url) - Serveur Cartelie injoignable, réessayer plus tard ... !')
			return

		xmin, ymin, xmax, ymax = result["xmin"], result["ymin"], result["xmax"], result["ymax"]
		transformer = self.getTransformer(2154)
		rect = transformer.transformBoundingBox(QgsRectangle(xmin, ymin, xmax, ymax), QgsCoordinateTransform.ForwardTransform)
		# Zoomer sur le rectangle
		self.zoomTo(rect.xMinimum() , rect.yMaximum () , rect.xMaximum(), rect.yMinimum())

		
	def getTransformer(self, epsgCode):
		transformer = QgsCoordinateTransform()
		transformer.setSourceCrs(QgsCoordinateReferenceSystem(epsgCode))
		destCrs = QgsProject.instance().crs() 
		transformer.setDestinationCrs(destCrs)
		return transformer

	def getMarkerLocationInMapCoordinates(self, marker):
		x, y  = marker.x(), marker.y()
		point = marker.toMapCoordinates(QPoint(x, y))
		return point.x(), point.y()

	def setMarker(self):
		if self.marker :
			mc = self.iface.mapCanvas()
			x, y = self.getMarkerLocationInMapCoordinates(self.marker)
			self.cleanMarker()
			self.marker = dynaLocationMarker(mc, x, y, self.color) if self.dlg.dynaMarker.isChecked() else basicLocationMarker(mc, x, y, self.color)
			self.tmpGeometry.append(self.marker)
			mc.refresh()

	def setColor(self): 
		alpha = int(self.dlg.opacityMarker.opacity()*255)        
		if self.dlg.sender() == self.dlg.colorMarker  : self.color.setRgb(self.dlg.colorMarker.color().rgb())
		elif self.dlg.sender() == self.dlg.opacityMarker : self.color.setAlpha(alpha)
		if self.marker and type(self.marker) != dynaLocationMarker : self.marker.setColor(self.color)


	def setScale(self):
		if self.marker :
				x, y = self.getMarkerLocationInMapCoordinates(self.marker)
				mc = self.iface.mapCanvas()
				# modif 2.2 gestion exception et mis a echelle 0
				try:
						if self.iface.mapCanvas().mapUnits()==0: scale = self.dlg.scale.value() #si unités en metres
						else: scale = 0 #pas des metres donc on ne peut pas utiliser ce parametre.
				except Exception as e:
						scale = 0
				rect = QgsRectangle(x -scale, y -scale, x + scale, y + scale)
				mc.setExtent(rect)
				mc.refresh()

	def zoomTo(self, x, y, x1, y1):
		# Zoomer sur rectangle englobant
		mc = self.iface.mapCanvas()
		# modif 2.2 gestion exception et mis a echelle 0
		try:
			if self.iface.mapCanvas().mapUnits()==0: scale = self.dlg.scale.value() #si unités en metres
			else: scale = 0 #pas des metres donc on ne peut pas utiliser ce parametre.
		except Exception as e: scale = 0
		
		rect = QgsRectangle(x -scale, y -scale, x1 + scale, y1 + scale)
		mc.setExtent(rect)

		self.cleanMarker()
		
		self.marker = dynaLocationMarker(mc, rect.center().x(), rect.center().y(), self.color) if self.dlg.dynaMarker.isChecked() else basicLocationMarker(mc, rect.center().x(), rect.center().y(), self.color)
		self.tmpGeometry.append(self.marker)
		mc.refresh()

		# Memoriser le parametre "Zoom" :
		s = QSettings()
		scale = self.dlg.scale.value()
		s.setValue("%szoom" % self.settings, scale )

	def updateCodecity(self, idx):
		"""met à jour le citycode pour filtrer la requete par code insee"""
		
		c = self.results[2][idx-1]["code"]
		self.dlg.adrin.set_codecity(c)
		
	def getAbout(self):
				icon = join(dirname(__file__), "icone.png")
				html = "Ce plugin exploite (par le protocole <b>HTTP</b>):<br>"
				html+= "<ol><li>le service Web du Ministère de la Transition Ecologique et Solidaire de géolocalisation avec plusieurs échelles administratives (Région, Département, Commune, Section, Parcelle) ;</li>"
				html+= "<li>le service web de géolocalisation à l'adresse Etalab.gouv.fr-BAN.</li></ol>"
				html+= "Ces deux web services fonctionnent depuis des postes de travail ayant un accès internet, en utilisant la configuration réseau de qgis pour le protocole HTTP et le système de projection courant du projet pour toute transformation des coordonnées.<br><br>"
				html+= "<img src='%s'> Version 3.2 - juillet 2018" % icon.replace("\\", "/")
				QMessageBox.information(self.dlg,"A propos", "%s" % html)



class basicLocationMarker(QgsVertexMarker):
	def __init__(self, canvas, x, y, color):
		super(basicLocationMarker, self).__init__(canvas)
		self.canvas = canvas
		self.color = color
		self.map_pos = QgsPointXY(x, y)
		self.setCenter(self.map_pos)
		self.setColor(self.color)
		self.setIconSize(10)
		self.setIconType(QgsVertexMarker.ICON_X) # ICON_BOX ICON_CROSS, ICON_X
		self.setPenWidth(2)



class dynaLocationMarker(QgsMapCanvasItem):
	class aniObject(QObject):
		def __init__(self):
			super(dynaLocationMarker.aniObject, self).__init__()
			self._size = 0
			self.startsize = 0
			self.maxsize = 32

		@pyqtProperty(int)
		def size(self): return self._size

		@size.setter
		def size(self, value): self._size = value

	def __init__(self, canvas, x, y, color):
		self.canvas = canvas
		self.color = color
		self.map_pos = QgsPointXY(x, y)
		self.aniObject = dynaLocationMarker.aniObject()
		QgsMapCanvasItem.__init__(self, canvas)
		self.anim = QPropertyAnimation(self.aniObject, b"size")
		self.anim.setDuration(1000)
		self.anim.setStartValue(self.aniObject.startsize)
		self.anim.setEndValue(self.aniObject.maxsize)
		self.anim.setLoopCount(-1)
		self.anim.valueChanged.connect(self.value_changed)
		self.anim.start()

	@property
	def size(self): return self.aniObject.size
	@property
	def halfsize(self): return self.aniObject.maxsize / 2.0
	@property
	def maxsize(self): return self.aniObject.maxsize
	def value_changed(self, value): self.update()

	def paint(self, painter, xxx, xxx2):
		self.setCenter(self.map_pos)
		rect = QRectF(0 - self.halfsize, 0 - self.halfsize, self.size, self.size)
		painter.setRenderHint(QPainter.Antialiasing)
		painter.setBrush(self.color) 
		painter.setPen(self.color) 
		painter.drawEllipse(QPointF(0,0), self.size, self.size)

	def boundingRect(self): return QRectF(-self.halfsize * 2.0, -self.halfsize * 2.0, 2.0 * self.maxsize, 2.0 * self.maxsize)
	def setCenter(self, map_pos): self.map_pos = map_pos; self.setPos(self.toCanvasCoordinates(self.map_pos))
	def updatePosition(self): self.setCenter(self.map_pos)

