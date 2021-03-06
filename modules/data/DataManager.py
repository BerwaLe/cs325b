import os

import tensorflow as tf
import pandas as pd
import numpy as np

from sklearn.utils.class_weight import compute_class_weight
from tensorflow.keras.preprocessing.image import ImageDataGenerator

import modules

class DataManager:
    
    def __init__(self, config):
        self.config = config
        
        self.filenames = {}
        self.dataframes = {}
        self.shapefiles = {}
        
        self._setup_countries = set()
        
        if config["use_kenya_images"]:
            self._setup("kenya")
        
        if config["use_peru_images"]:
            self._setup("peru")


    def _setup(self, country):
        if country != "kenya" and country != "peru":
            raise ValueError("Country must be either \'kenya\' or \'peru\'.")

        if country not in self._setup_countries:
            geo = modules.data.load_geodata(country)
            osm, sf = modules.data.load_shapefile(country)

            self.shapefiles[country] = sf
            self.dataframes[country] = pd.DataFrame.merge(geo, osm, on="index")

            classes = [self.config["class_enum"][v] for v in self.dataframes[country]["class"].values]
            self.dataframes[country]["label"] = classes
            
            directory = f"{modules.data.util.root()}/{country}/{self.config['image_size']}/{self.config['resizing']}"
            valid = self.file_is_valid(self.dataframes[country], directory)
            self.dataframes[country] = self.dataframes[country][valid]

            self._setup_countries.add(country)

    def file_is_valid(self, dataframe, directory):
        filenames = self._extract_filenames(directory)
        valid = []
        for i in dataframe.index:
            fname = self._format_filename(i, int(dataframe['id'].loc[i]))
            valid.append(fname in filenames)
        return np.array(valid)

    def class_weight(self, country):
        class_weight = None

        if self.config["weight_classes"]:
            class_weight = compute_class_weight(
                "balanced", np.arange(self.config["n_classes"]), self.dataframes[country]["label"].values
            )

        return class_weight
    
    def sample_class(self, dataframe, cls, n):           
        df = dataframe[dataframe["class"] == cls]
        return df.sample(n=n, replace=False, random_state=self.config["seed"])
            
    def generate_kenya(self):
        
        # get input directory
        directory = f"{modules.data.util.root()}/kenya/{self.config['image_size']}/{self.config['resizing']}"
        
        # format dataframe for ImageDataGenerator.flow_from_dataframe
        dataframe = self._format_dataframe_for_flow("kenya")
        
        if self.config['remove_clouds']:
            cloud_directory = f"{modules.data.util.root()}/kenya/cloudy.txt"
            cloud_filenames = pd.read_csv(cloud_directory, sep=" ", header=None)
            cloud_filenames.columns = ["filename"]
            
            cloud_filenames["filename"] = cloud_filenames.filename.str.slice(16)
            cloud_filenames["filename"] = cloud_filenames.filename.str.slice(0, -4) + ".jpg"

            dataframe = dataframe[~dataframe['filename'].isin(cloud_filenames.filename)]
            
            print("Declouded dataframe length: " + str(len(dataframe.index)))
        
        # sample the data
        if self.config["sample"]:                    
            if not self.config["sample"]["balanced"]:
                dataframe = dataframe.sample(n=self.config["sample"]["size"], replace=False, random_state=self.config["seed"])
            else:
                labels = set()
                for cls in self.config["class_enum"]:
                    if self.config["class_enum"][cls] >= 0:
                        labels.add(str(self.config["class_enum"][cls]))
                dataframes_per_class = []
                for label in labels:
                    df = self.sample_class(dataframe, label, (self.config["sample"]["size"] // len(labels)))
                    dataframes_per_class.append(df)
                dataframe = pd.concat(dataframes_per_class)
                
                # shuffle the data
                dataframe = dataframe.reindex(np.random.permutation(dataframe.index))        

        # define data preprocessing
        preprocessing_function = None
        if self.config["pretrained"]:
            module = modules.models.pretrained_cnn_module(self.config["pretrained"]["type"])
            preprocessing_function = getattr(module, "preprocess_input")
        else:
            raise NotImplementedError("Custom model and preprocessing pipeline not yet defined.")
        datagen = None
        
        if self.config["use_grayscale"]:
            def new_preprocess(x):
                x = tf.image.rgb_to_grayscale(x)
                return preprocessing_function(x)
            # make image data generator for rgb
            datagen = ImageDataGenerator(
                preprocessing_function=new_preprocess,
                validation_split=self.config["validation_split"],
            )
            
        else:
            # make image data generator for rgb
            datagen = ImageDataGenerator(
                preprocessing_function=preprocessing_function,
                validation_split=self.config["validation_split"],
            )
        
        if self.config["mask"] == 'none':
            train_generator = self._build_generator(datagen, dataframe, directory, "training")
            val_generator = self._build_generator(datagen, dataframe, directory, "validation")            
        elif self.config["mask"] == "occlude" or self.config["mask"] == "overlay" or self.config["mask"] == "overlay_3":
            datagen_mask = ImageDataGenerator(validation_split=self.config["validation_split"])
            dataframe_mask = pd.DataFrame(
                list(map(
                    lambda e: (f"{e[0]}_kenya_224x224_mask_20.png", e[2]), 
                    zip(
                        self.dataframes['kenya'].index,
                        map(int, self.dataframes['kenya']["id"]),
                        self.dataframes['kenya']["class"]
                    )
                )),
                columns=["filename", "class"])
            dataframe_mask = dataframe_mask.iloc[dataframe.index]
            directory_mask = f"{modules.data.util.root()}/kenya/kenya_224x224_masks_20/"

            train_generator = self.multiple_generator(
                datagen, datagen_mask, 
                dataframe, dataframe_mask, 
                directory, directory_mask, 
                'training'
            )
            val_generator = self.multiple_generator(
                datagen, datagen_mask, 
                dataframe, dataframe_mask, 
                directory, directory_mask, 
                'validation'
            )
               
        return train_generator, val_generator, dataframe


    def generate_peru(self):
        directory = f"{modules.data.util.root()}/peru/{self.config['image_size']}/{self.config['resizing']}"
        country="peru"

        geo = modules.data.load_geodata(country)
        geo = geo.iloc[::2].reset_index()
        geo['index'] = geo['index'] / 2
        osm, sf = modules.data.load_shapefile(country)

        self.shapefiles[country] = sf
        self.dataframes[country] = pd.DataFrame.merge(geo, osm, on="index")
        self.dataframes[country]['index'] = (self.dataframes[country]['index'] * 2).astype('int32')
        self.dataframes[country] = self.dataframes[country].set_index(self.dataframes[country]['index'])
        dataframe = self._format_dataframe_for_flow("peru")
        dir_names = os.listdir(directory)
        dataframe['filename'] = dataframe['filename'].str.split('_', n = 1, expand = True)[1]
        dir_names = pd.DataFrame(dir_names)[0].str.split('_', n=1, expand=True).drop_duplicates(1, keep='last')
        dir_names = dir_names.rename(columns={0: "prefix", 1: "filename"})
        dataframe = pd.merge(dataframe, dir_names, on='filename')
        dataframe['filename'] = dataframe['prefix'] + '_' + dataframe['filename']
        dataframe = dataframe.drop(columns='prefix')
        
        if self.config['remove_clouds']:
            cloud_directory = f"{modules.data.util.root()}/peru/cloudy.txt"
            cloud_filenames = pd.read_csv(cloud_directory, sep=" ", header=None)
            cloud_filenames.columns = ["filename"]
            
            cloud_filenames["filename"] = cloud_filenames.filename.str.slice(16)
            cloud_filenames["filename"] = cloud_filenames.filename.str.slice(0, -4) + ".jpg"

            dataframe = dataframe[~dataframe['filename'].isin(cloud_filenames.filename)]
            
            print("Declouded dataframe length: " + str(len(dataframe.index)))
            
        # sample the data
        if self.config["sample"]:
            if not self.config["sample"]["balanced"]:
                dataframe = dataframe.sample(n=self.config["sample"]["size"], replace=False, random_state=self.config["seed"])
            else:
                dataframes_per_class = []
                for cls in self.config["class_enum"]:
                    df = self.sample_class(dataframe, cls, (self.config["sample"]["size"] // self.config["n_classes"]))
                    dataframes_per_class.append(df)
                dataframe = pd.concat(dataframes_per_class)
                
                # shuffle the data
                dataframe = dataframe.reindex(np.random.permutation(dataframe.index))        

        # define data preprocessing
        preprocessing_function = None
        if self.config["pretrained"]:
            module = modules.models.pretrained_cnn_module(self.config["pretrained"]["type"])
            preprocessing_function = getattr(module, "preprocess_input")
        else:
            raise NotImplementedError("Custom model and preprocessing pipeline not yet defined.")
        
        # make image data generator for rgb
        datagen = ImageDataGenerator(
            preprocessing_function=preprocessing_function,
            validation_split=self.config["validation_split"],
        )
        
        if self.config["mask"] == 'none':
            train_generator = self._build_generator(datagen, dataframe, directory, "training")
            val_generator = self._build_generator(datagen, dataframe, directory, "validation")            
        elif self.config["mask"] == "occlude" or self.config["mask"] == "overlay":
            raise NotImplementedError("Masking not implemented for Peru.")
               
        return train_generator, val_generator, dataframe

        
    def multiple_generator(self, datagen1, datagen2, dataframe1, dataframe2, directory1, directory2, subset):
        generator1 = self._build_generator(datagen1, dataframe1, directory1, subset)
        generator2 = self._build_generator(datagen2, dataframe2, directory2, subset)

        while True:
            x1, y1 = generator1.next()
            x2, y2 = generator2.next()
            if self.config["mask"] == "occlude":
                if not self.config['mask_inverted']:
                    yield (x1 * np.flip(x2, axis=1)).astype(np.float32), y1
                else:
                    yield (x1 * (1 - np.flip(x2, axis=1))).astype(np.float32), y1
            elif self.config["mask"] == "overlay":
                yield np.concatenate((x1, np.expand_dims(np.flip(x2, axis=1)[:, :, :, 0], axis=3)), axis=3), y1
            elif self.config["mask"] == "overlay_3":
                yield np.concatenate((x1[:, :, :, :-1], np.expand_dims(np.flip(x2, axis=1)[:, :, :, 0], axis=3)), axis=3), y1
    def _build_generator(self, datagen, dataframe, directory, subset):
        to_shuffle = True
        if subset == "validation":
            to_shuffle = False
            
        return datagen.flow_from_dataframe(
            dataframe,
            directory=directory, 
            subset=subset,
            class_mode='categorical',
            batch_size=self.config["batch_size"],
            seed=self.config["seed"],
#             shuffle=self.config["shuffle"],
            shuffle=to_shuffle,
            target_size=(self.config["image_size"], self.config["image_size"])
        )
            
    def _format_dataframe_for_flow(self, country, suffix=None):
        if country == 'kenya':
            return pd.DataFrame(
                list(map(
                    lambda e: (self._format_filename(e[0], e[1], suffix=suffix), e[2]), 
                    zip(
                        self.dataframes[country].index, 
                        map(int, self.dataframes[country]["id"]),
                        map(str, self.dataframes[country]["label"]),
                    )
                )),
                columns=["filename", "class"]
            )
        
        if country == 'peru':
            return pd.DataFrame(
                list(map(
                    lambda e: (self._format_filename(e[0], e[1], suffix=suffix), e[2]), 
                    zip(
                        self.dataframes[country].index, 
                        map(int, self.dataframes[country]["id"]),
                        self.dataframes[country]["class"]
                    )
                )),
                columns=["filename", "class"]
            )
            
    def _format_filename(self, id1, id2, suffix=None, ext="jpg"):
        if suffix is None:
            return f"{id1}_{id2}.{ext}"
        else:
            return f"{id1}_{id2}_{suffix}.{ext}"
    
    def _extract_filenames(self, directory):
        filenames = map(lambda x: x.strip(), os.listdir(directory))
        filenames = set(filenames)
        return filenames
# import os

# import tensorflow as tf
# import pandas as pd
# import numpy as np

# from sklearn.utils.class_weight import compute_class_weight
# from tensorflow.keras.preprocessing.image import ImageDataGenerator

# import modules

# class DataManager:
    
#     def __init__(self, config):
#         self.config = config
        
#         self.filenames = {}
#         self.dataframes = {}
#         self.shapefiles = {}
        
#         self._setup_countries = set()
        
#         if config["use_kenya_images"]:
#             self._setup("kenya")
        
#         if config["use_peru_images"]:
#             self._setup("peru")


#     def _setup(self, country):
#         if country != "kenya" and country != "peru":
#             raise ValueError("Country must be either \'kenya\' or \'peru\'.")

#         if country not in self._setup_countries:
#             geo = modules.data.load_geodata(country)
#             osm, sf = modules.data.load_shapefile(country)

#             self.shapefiles[country] = sf
#             self.dataframes[country] = pd.DataFrame.merge(geo, osm, on="index")

#             classes = [self.config["class_enum"][v] for v in self.dataframes[country]["class"].values]
#             self.dataframes[country]["label"] = classes
            
#             directory = f"{modules.data.util.root()}/{country}/{self.config['image_size']}/{self.config['resizing']}"
#             valid = self.file_is_valid(self.dataframes[country], directory)
#             self.dataframes[country] = self.dataframes[country][valid]

#             self._setup_countries.add(country)

#     def file_is_valid(self, dataframe, directory):
#         filenames = self._extract_filenames(directory)
#         valid = []
#         for i in dataframe.index:
#             fname = self._format_filename(i, int(dataframe['id'].loc[i]))
#             valid.append(fname in filenames)
#         return np.array(valid)

#     def class_weight(self, country):
#         class_weight = None

#         if self.config["weight_classes"]:
#             class_weight = compute_class_weight(
#                 "balanced", np.arange(self.config["n_classes"]), self.dataframes[country]["label"].values
#             )

#         return class_weight
    
#     def sample_class(self, dataframe, cls, n):
#         assert type(cls) == type("class")
        
#         df = dataframe[dataframe["class"] == cls]
#         return df.sample(n=n, replace=False, random_state=self.config["seed"])
    
#     def generate_kenya(self):
        
#         # get input directory
#         directory = f"{modules.data.util.root()}/kenya/{self.config['image_size']}/{self.config['resizing']}"
        
#         # format dataframe for ImageDataGenerator.flow_from_dataframe
#         dataframe = self._format_dataframe_for_flow("kenya")
        
#         if self.config['remove_clouds']:
#             cloud_directory = f"{modules.data.util.root()}/kenya/cloudy.txt"
#             cloud_filenames = pd.read_csv(cloud_directory, sep=" ", header=None)
#             cloud_filenames.columns = ["filename"]
            
#             cloud_filenames["filename"] = cloud_filenames.filename.str.slice(16)
#             cloud_filenames["filename"] = cloud_filenames.filename.str.slice(0, -4) + ".jpg"

#             dataframe = dataframe[~dataframe['filename'].isin(cloud_filenames.filename)]
            
#             print("Declouded dataframe length: " + str(len(dataframe.index)))
        
#         # sample the data
#         if self.config["sample"]:
#             if not self.config["sample"]["balanced"]:
#                 dataframe = dataframe.sample(n=self.config["sample"]["size"], replace=False, random_state=self.config["seed"])
#             else:
#                 dataframes_per_class = []
#                 for cls in self.config["class_enum"]:
#                     df = self.sample_class(dataframe, cls, (self.config["sample"]["size"] // self.config["n_classes"]))
#                     dataframes_per_class.append(df)
#                 dataframe = pd.concat(dataframes_per_class)
                
#                 # shuffle the data
#                 dataframe = dataframe.reindex(np.random.permutation(dataframe.index))        

#         # define data preprocessing
#         preprocessing_function = None
#         if self.config["pretrained"]:
#             module = modules.models.pretrained_cnn_module(self.config["pretrained"]["type"])
#             preprocessing_function = getattr(module, "preprocess_input")
#         else:
#             raise NotImplementedError("Custom model and preprocessing pipeline not yet defined.")
#         datagen = None
        
#         if self.config["use_grayscale"]:
#             def new_preprocess(x):
#                 x = tf.image.rgb_to_grayscale(x)
#                 return preprocessing_function(x)
#             # make image data generator for rgb
#             datagen = ImageDataGenerator(
#                 preprocessing_function=new_preprocess,
#                 validation_split=self.config["validation_split"],
#             )
            
#         else:
#             # make image data generator for rgb
#             datagen = ImageDataGenerator(
#                 preprocessing_function=preprocessing_function,
#                 validation_split=self.config["validation_split"],
#             )
        
#         if self.config["mask"] == 'none':
#             train_generator = self._build_generator(datagen, dataframe, directory, "training")
#             val_generator = self._build_generator(datagen, dataframe, directory, "validation")            
#         elif self.config["mask"] == "occlude" or self.config["mask"] == "overlay" or self.config["mask"] == "overlay_3":
#             datagen_mask = ImageDataGenerator(validation_split=self.config["validation_split"])
#             dataframe_mask = pd.DataFrame(
#                 list(map(
#                     lambda e: (f"{e[0]}_kenya_224x224_mask_20.png", e[2]), 
#                     zip(
#                         self.dataframes['kenya'].index,
#                         map(int, self.dataframes['kenya']["id"]),
#                         self.dataframes['kenya']["class"]
#                     )
#                 )),
#                 columns=["filename", "class"])
#             dataframe_mask = dataframe_mask.iloc[dataframe.index]
#             directory_mask = f"{modules.data.util.root()}/kenya/kenya_224x224_masks_20/"

#             train_generator = self.multiple_generator(
#                 datagen, datagen_mask, 
#                 dataframe, dataframe_mask, 
#                 directory, directory_mask, 
#                 'training'
#             )
#             val_generator = self.multiple_generator(
#                 datagen, datagen_mask, 
#                 dataframe, dataframe_mask, 
#                 directory, directory_mask, 
#                 'validation'
#             )
               
#         return train_generator, val_generator, dataframe


#     def generate_peru(self):
#         directory = f"{modules.data.util.root()}/peru/{self.config['image_size']}/{self.config['resizing']}"
#         country="peru"

#         geo = modules.data.load_geodata(country)
#         geo = geo.iloc[::2].reset_index()
#         geo['index'] = geo['index'] / 2
#         osm, sf = modules.data.load_shapefile(country)

#         self.shapefiles[country] = sf
#         self.dataframes[country] = pd.DataFrame.merge(geo, osm, on="index")
#         self.dataframes[country]['index'] = (self.dataframes[country]['index'] * 2).astype('int32')
#         self.dataframes[country] = self.dataframes[country].set_index(self.dataframes[country]['index'])
#         dataframe = self._format_dataframe_for_flow("peru")
#         dir_names = os.listdir(directory)
#         dataframe['filename'] = dataframe['filename'].str.split('_', n = 1, expand = True)[1]
#         dir_names = pd.DataFrame(dir_names)[0].str.split('_', n=1, expand=True).drop_duplicates(1, keep='last')
#         dir_names = dir_names.rename(columns={0: "prefix", 1: "filename"})
#         dataframe = pd.merge(dataframe, dir_names, on='filename')
#         dataframe['filename'] = dataframe['prefix'] + '_' + dataframe['filename']
#         dataframe = dataframe.drop(columns='prefix')
        
#         if self.config['remove_clouds']:
#             cloud_directory = f"{modules.data.util.root()}/peru/cloudy.txt"
#             cloud_filenames = pd.read_csv(cloud_directory, sep=" ", header=None)
#             cloud_filenames.columns = ["filename"]
            
#             cloud_filenames["filename"] = cloud_filenames.filename.str.slice(16)
#             cloud_filenames["filename"] = cloud_filenames.filename.str.slice(0, -4) + ".jpg"

#             dataframe = dataframe[~dataframe['filename'].isin(cloud_filenames.filename)]
            
#             print("Declouded dataframe length: " + str(len(dataframe.index)))
            
#         # sample the data
#         if self.config["sample"]:
#             if not self.config["sample"]["balanced"]:
#                 dataframe = dataframe.sample(n=self.config["sample"]["size"], replace=False, random_state=self.config["seed"])
#             else:
#                 dataframes_per_class = []
#                 for cls in self.config["class_enum"]:
#                     df = self.sample_class(dataframe, cls, (self.config["sample"]["size"] // self.config["n_classes"]))
#                     dataframes_per_class.append(df)
#                 dataframe = pd.concat(dataframes_per_class)
                
#                 # shuffle the data
#                 dataframe = dataframe.reindex(np.random.permutation(dataframe.index))        

#         # define data preprocessing
#         preprocessing_function = None
#         if self.config["pretrained"]:
#             module = modules.models.pretrained_cnn_module(self.config["pretrained"]["type"])
#             preprocessing_function = getattr(module, "preprocess_input")
#         else:
#             raise NotImplementedError("Custom model and preprocessing pipeline not yet defined.")
        
#         # make image data generator for rgb
#         datagen = ImageDataGenerator(
#             preprocessing_function=preprocessing_function,
#             validation_split=self.config["validation_split"],
#         )
        
#         if self.config["mask"] == 'none':
#             train_generator = self._build_generator(datagen, dataframe, directory, "training")
#             val_generator = self._build_generator(datagen, dataframe, directory, "validation")            
#         elif self.config["mask"] == "occlude" or self.config["mask"] == "overlay":
#             raise NotImplementedError("Masking not implemented for Peru.")
               
#         return train_generator, val_generator, dataframe

        
#     def multiple_generator(self, datagen1, datagen2, dataframe1, dataframe2, directory1, directory2, subset):
#         generator1 = self._build_generator(datagen1, dataframe1, directory1, subset)
#         generator2 = self._build_generator(datagen2, dataframe2, directory2, subset)
#         while True:
#             x1, y1 = generator1.next()
#             x2, y2 = generator2.next()
#             if self.config["mask"] == "occlude":
#                 if not self.config['mask_inverted']:
#                     yield (x1 * np.flip(x2, axis=1)).astype(np.float32), y1
#                 else:
#                     yield (x1 * (1 - np.flip(x2, axis=1))).astype(np.float32), y1
#             elif self.config["mask"] == "overlay":
#                 yield np.concatenate((x1, np.expand_dims(np.flip(x2, axis=1)[:, :, :, 0], axis=3)), axis=3), y1
#             elif self.config["mask"] == "overlay_3":
#                 yield np.concatenate((x1[:, :, :, :-1], np.expand_dims(np.flip(x2, axis=1)[:, :, :, 0], axis=3)), axis=3), y1
#     def _build_generator(self, datagen, dataframe, directory, subset):
#         return datagen.flow_from_dataframe(
#             dataframe,
#             directory=directory, 
#             subset=subset,
#             class_mode='categorical',
#             batch_size=self.config["batch_size"],
#             seed=self.config["seed"],
# #             shuffle=self.config["shuffle"],
#             shuffle=False,
#             target_size=(self.config["image_size"], self.config["image_size"])
#         )
            
#     def _format_dataframe_for_flow(self, country, suffix=None):
#         return pd.DataFrame(
#             list(map(
#                 lambda e: (self._format_filename(e[0], e[1], suffix=suffix), e[2]), 
#                 zip(
#                     self.dataframes[country].index, 
#                     map(int, self.dataframes[country]["id"]),
#                     self.dataframes[country]["class"]
#                 )
#             )),
#             columns=["filename", "class"]
#         )
            
#     def _format_filename(self, id1, id2, suffix=None, ext="jpg"):
#         if suffix is None:
#             return f"{id1}_{id2}.{ext}"
#         else:
#             return f"{id1}_{id2}_{suffix}.{ext}"
    
#     def _extract_filenames(self, directory):
#         filenames = map(lambda x: x.strip(), os.listdir(directory))
#         filenames = set(filenames)
#         return filenames