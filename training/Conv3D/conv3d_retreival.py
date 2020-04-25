# imports
import warnings
warnings.filterwarnings('ignore')

import os, cv2, shutil, json
import numpy as np, pandas as pd, pickle as pkl

from glob import glob
from time import time
from datetime import datetime
from tqdm import tqdm

from sklearn.metrics import precision_recall_curve, average_precision_score, accuracy_score
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import train_test_split

from keras.applications.mobilenet import MobileNet
from keras.models import Model, load_model
from keras.layers import LSTM, Dense, InputLayer
from keras.callbacks.callbacks import ModelCheckpoint

from c3d_model import c3d_model

from matplotlib import pyplot as plt
from scipy.spatial.distance import euclidean, mahalanobis, cosine


class DataHandler:
	'''
	Handles all operations with respect to data
	'''
	def __init__(self, videos_path = "/mnt/E2F262F2F262C9FD/PROJECTS/media_retrieval/Datasets/KTH/train/", test_size = 0.05):
		'''
		Initalizes the class variables for data handling
		'''
		self.n_frames = 16
		self.operating_resolution = (224, 224)
		self.test_split = test_size

		self.videos_path = videos_path

	def sample_frames(self, video_path):
		'''
		Gets 'n' number of frames, each of resolution 'w' x 'h' and 3 channels (RGB) from a video

		Uses equidistant sampling of frames
		'''
		cap = cv2.VideoCapture(video_path)
		read_count = 1
		frames_list = list()
		frame_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

		while cap.isOpened():
			isRead, frame = cap.read()
			if not isRead: break
			if read_count % (int(frame_total/self.n_frames) -5) == 0:
				frame = cv2.resize(frame, self.operating_resolution)
				frames_list.append(frame)
			read_count += 1
			if len(frames_list) == self.n_frames: break
		return np.array(frames_list[:self.n_frames])

	def extract_video_features(self, video_file):
		'''
		Returns array of fram features for a video
		'''
		return self.sample_frames(video_file)

	def prepare_training_data(self, videos_path):
		'''
		Returns data and labels for all videos in a directory
		'''
		folders = sorted(os.listdir(videos_path))
		classes = dict([(folder, idx) for idx, folder in enumerate(folders)])
		n_classes = len(classes)

		frame_features = list()
		labels = list()
		videos_list = list()

		for folder in folders:
			folder_path = os.path.join(self.videos_path, folder)
			video_files = sorted(glob(os.path.join(folder_path, "*")))

			for video_file in video_files:
				frame_features.append(self.extract_video_features(video_file))
				labels.append(classes[folder])
				videos_list.append(video_file)

		return np.array(frame_features), np.array(labels), np.array(videos_list), classes


	def get_training_data(self, save_data_as = None, data_pickle = None):
		'''
		Prepares the preprocessed training data and labels
		'''
		if data_pickle == None:
			if save_data_as == None: save_data_as = "data.pkl"
			if ".pkl" not in save_data_as: save_data_as += ".pkl"

			X, y, video_list, classes = self.prepare_training_data(self.videos_path)

			X_train, X_test, y_train, y_test = train_test_split(X, y, test_size = self.test_split, random_state=42)

			pkl.dump({"X_train": X_train, "y_train": y_train, "X_test": X_test, "y_test": y_test, "classes": classes, "videos": video_list}, open(save_data_as, "wb"))
			print("saved data to ", save_data_as)
		else:
			data_dict = pkl.load(open(data_pickle, "rb"))
			X_train, y_train, X_test, y_test, video_list, classes = data_dict["X_train"], data_dict["y_train"], data_dict["X_test"], data_dict["y_test"], data_dict["videos"], data_dict["classes"]

		return X_train, X_test, y_train, y_test, video_list, classes


class Trainer(DataHandler):
	'''
	Handles all the training operations
	'''
	def __init__(self, data_to_use = "/mnt/E2F262F2F262C9FD/PROJECTS/media_retrieval/training/Conv3D/KTH_data.pkl", operating_resolution = (224, 224)):
		'''
		Initializes the training class variables
		'''
		DataHandler.__init__(self)
		self.operating_resolution = operating_resolution
		self.training_version = str(datetime.now())[:16].replace("-", "_").replace(" ", "_")
		os.mkdir(self.training_version)
		save_data_as = None
		if data_to_use == None:
			save_data_as = os.path.join(self.training_version,  "data.pkl")

		self.X_train, self.X_test, self.y_train, self.y_test, self.videos, self.classes = self.get_training_data(save_data_as = save_data_as, data_pickle = data_to_use)
		self.n_classes = len(self.classes)


		# training params
		self.epochs = 50
		self.batch_size = 32
		self.validation_split = 0.05

	def train(self, pretrained_model = None, model_path = None):
		'''
		Runs the training
		'''
		if pretrained_model != None: self.c3d_model = load_model(pretrained_model)
		else: self.c3d_model = c3d_model(resolution = self.operating_resolution, n_frames = 16, channels = 3, nb_classes = 3)

		if model_path == None: model_path = "C3D_E{epoch:02d}_VA{val_accuracy:.2f}.hdf5"
		model_path = os.path.join(self.training_version, model_path)

		callbacks = [ModelCheckpoint(model_path, monitor='val_accuracy', verbose=1, save_best_only=True, mode='max')]
		
		self.c3d_model.compile(optimizer='adam', loss='sparse_categorical_crossentropy')
		self.c3d_model.fit(self.X_train, self.y_train, epochs=self.epochs, batch_size=self.batch_size, validation_split=self.validation_split, shuffle=True, verbose=2, callbacks = callbacks)


class Tester(DataHandler):
	def __init__(self, test_model):
		DataHandler.__init__(self, videos_path = "/content/drive/My Drive/data/KTH_data/c3d_model_KTH_HAR.hdf5", test_size = 0.01)
		self.X_train, self.X_test, self.y_train, self.y_test, self.video_list, self.classes = self.get_training_data(self, data_pickle = "/content/drive/My Drive/data/KTH_data/test_data.pkl")
		conv3_model = load_model(test_model)
		self.test_model = self.get_submodel(conv3_model, "dense_2")
		self.distance = cosine_similarity
		self.distance_threshold = 0.9

	def get_submodel(self, main_model, output_layer = "fc1"):
		return Model(inputs=main_model.inputs, output=main_model.get_layer(output_layer).output)

	def test(self):
		feature_vectors = self.test_model.predict(self.X_train, batch_size=4)
		distances = self.distance(feature_vectors, feature_vectors)
		accuracy_list = list()
		for idx, row in enumerate(distances):
			retrieved_indexes = np.squeeze(np.argwhere(row > self.distance_threshold))
			try:
				retrieved_labels = self.y_train[retrieved_indexes]
				actual_labels = np.squeeze([self.y_train[idx]] * len(retrieved_labels))
				accuracy = accuracy_score(actual_labels, retrieved_labels)
				accuracy_list.append(accuracy)
			except:
				continue
		accuracy_list = np.array(accuracy_list)
		print("Mean Retrieval Accuracy", np.mean(accuracy_list))

if __name__ == '__main__':
	tr = Trainer()
	tr.train()
	ts = Tester("/mnt/E2F262F2F262C9FD/PROJECTS/media_retrieval/training/Conv3D/models/c3d_model_KTH_HAR.hdf5")
	ts.test()