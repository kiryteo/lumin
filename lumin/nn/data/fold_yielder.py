import numpy as np
import pandas as pd
import h5py
from typing import Dict, Optional, Union, List
import warnings

'''
Todo:
- Add categorical features
- Add method to FoldYielder to import other data into correct format, e.g. csv, root
- Make HEPAugFoldYielder able to augment targets as well
'''


class FoldYielder:
    def __init__(self, source_file:h5py.File):
        self.augmented = False
        self.aug_mult = 0
        self.train_time_aug = False
        self.test_time_aug = False
        self.set_source(source_file)

    def set_source(self, source_file:h5py.File) -> None:
        self.source = source_file
        self.n_folds = len(self.source)

    def get_fold(self, index:int) -> Dict[str,np.ndarray]:
        return self.get_data(n_folds=1, fold_id=index, use_newaxis=True)

    def get_fold_df(self, index:int, pred_name:str='preds', weight_name:str='weights') -> pd.DataFrame:
        data = pd.DataFrame()
        if f'fold_{index}/{weight_name}' in self.source: data['gen_weight'] = np.array(self.source[f'fold_{index}/{weight_name}'])
        if f'fold_{index}/targets'       in self.source: data['gen_target'] = np.array(self.source[f'fold_{index}/targets'])
        if f'fold_{index}/{pred_name}'   in self.source: data['pred']       = np.array(self.source[f'fold_{index}/{pred_name}'])
        return data

    def get_column(self, column:str, n_folds:Optional[int]=None, fold_id:Optional[int]=None, use_newaxis:bool=False) -> Union[np.ndarray, None]:
        if f'fold_0/{column}' not in self.source: return None

        if fold_id is None:
            data = []
            for i, fold in enumerate(self.source):
                if n_folds is not None and i >= n_folds: break
                data.append(np.array(self.source[f'{fold}/{column}']))
            data = np.concatenate(data)

        else:
            data = np.array(self.source[f'fold_{fold_id}/{column}'])
        return data[:, None] if data[0].shape is () and use_newaxis else data

    def get_data(self, n_folds:Optional[int]=None, fold_id:Optional[int]=None, use_newaxis:bool=False) -> Dict[str,np.ndarray]:
        return {'inputs': np.nan_to_num(self.get_column('inputs',  n_folds=n_folds, fold_id=fold_id, use_newaxis=True)),
                'targets':              self.get_column('targets', n_folds=n_folds, fold_id=fold_id, use_newaxis=True),
                'weights':              self.get_column('weights', n_folds=n_folds, fold_id=fold_id, use_newaxis=True)}

    def get_df(self, pred_name:str='pred', n_load:Optional[int]=None, fold_id:Optional[int]=None):
        data = pd.DataFrame()
        data['gen_target'] = self.get_column('targets', n_folds=n_load, fold_id=fold_id)
        data['gen_weight'] = self.get_column('weights', n_folds=n_load, fold_id=fold_id)
        data['pred']       = self.get_column(pred_name, n_folds=n_load, fold_id=fold_id)
        print(f'{len(data)} candidates loaded')
        return data


class HEPAugFoldYielder(FoldYielder):
    def __init__(self, source_file:h5py.File, feats:List[str],
                 rot_mult:int=2, random_rot:bool=False,
                 reflect_x:bool=False, reflect_y:bool=True, reflect_z:bool=True,
                 train_time_aug:bool=True, test_time_aug:bool=True):
        super().__init__(source_file)

        if rot_mult > 0 and not random_rot and rot_mult % 2 != 0:
            warnings.warn('Warning: rot_mult must currently be even for fixed rotations, adding an extra rotation multiplicity')
            rot_mult += 1

        self.input_feats,self.rot_mult,self.random_rot,self.reflect_x,self.reflect_y,self.reflect_z,self.train_time_aug,self.test_time_aug = feats,rot_mult,random_rot,reflect_x,reflect_y,reflect_z,train_time_aug,test_time_aug
        self.augmented = True
        self.reflect_axes = []
        self.aug_mult = 1
        self.vectors = [x[:-3] for x in self.input_feats if '_px' in x]

        if self.rot_mult:
            print("Augmenting via phi rotations")
            self.aug_mult = self.rot_mult

            if self.reflect_y:
                print("Augmenting via y flips")
                self.reflect_axes += ['_py']
                self.aug_mult *= 2
            
            if self.reflect_z:
                print("Augmenting via longitunidnal flips")
                self.reflect_axes += ['_pz']
                self.aug_mult *= 2
            
        else:
            if self.reflect_x:
                print("Augmenting via x flips")
                self.reflect_axes += ['_px']
                self.aug_mult *= 2

            if self.reflect_y:
                print("Augmenting via y flips")
                self.reflect_axes += ['_py']
                self.aug_mult *= 2
            
            if self.reflect_z:
                print("Augmenting via longitunidnal flips")
                self.reflect_axes += ['_pz']
                self.aug_mult *= 2

        print(f'Total augmentation multiplicity is {self.aug_mult}')
    
    def rotate(self, in_data:pd.DataFrame) -> None:
        for vector in self.vectors:
            in_data.loc[:, f'{vector}_pxtmp'] = in_data.loc[:, f'{vector}_px']*np.cos(in_data.loc[:, 'aug_angle'])-in_data.loc[:, f'{vector}_py']*np.sin(in_data.loc[:, 'aug_angle'])
            in_data.loc[:, f'{vector}_py']    = in_data.loc[:, f'{vector}_py']*np.cos(in_data.loc[:, 'aug_angle'])+in_data.loc[:, f'{vector}_px']*np.sin(in_data.loc[:, 'aug_angle'])
            in_data.loc[:, f'{vector}_px']    = in_data.loc[:, f'{vector}_pxtmp']
    
    def reflect(self, in_data:pd.DataFrame) -> None:
        for vector in self.vectors:
            for coord in self.reflect_axes:
                try:
                    cut = (in_data[f'aug{coord}'] == 1)
                    in_data.loc[cut, f'{vector}{coord}'] = -in_data.loc[cut, f'{vector}{coord}']
                except KeyError:
                    pass
            
    def get_fold(self, index:int) -> Dict[str,np.ndarray]:
        data = super().get_fold(index)         
        if not self.augmented: return data
        inputs = pd.DataFrame(np.array(self.source[f'fold_{index}/inputs']), columns=self.input_feats)

        if self.rot_mult:
            inputs['aug_angle'] = 2*np.pi*np.random.random(size=len(inputs))
            self.rotate(inputs)
            
        for coord in self.reflect_axes:
            inputs[f'aug{coord}'] = np.random.randint(0, 2, size=len(inputs))
        self.reflect(inputs)
            
        data['inputs'] = np.nan_to_num(inputs[self.input_feats].values)
        return data

    def _get_ref_index(self, aug_index:int) -> str:
        n_axes = len(self.reflect_axes)
        div = self.rot_mult if self.rot_mult else 1
        if   n_axes == 3: return '{0:03b}'.format(int(aug_index/div))
        elif n_axes == 2: return '{0:02b}'.format(int(aug_index/div))
        elif n_axes == 1: return '{0:01b}'.format(int(aug_index/div))
    
    def get_test_fold(self, index:int, aug_index:int) -> Dict[str, np.ndarray]:
        if aug_index >= self.aug_mult: raise ValueError(f"Invalid augmentation index passed {aug_index}")
        data = super().get_fold(index)         
        if not self.augmented: return data
        inputs = pd.DataFrame(np.array(self.source[f'fold_{index}/inputs']), columns=self.input_feats)
            
        if len(self.reflect_axes) > 0 and self.rot_mult > 0:
            rot_index = aug_index % self.rot_mult
            ref_index = self._get_ref_index(aug_index)
            #print('\nAug index = ', aug_index)
            if self.random_rot: inputs['aug_angle'] = 2*np.pi*np.random.random(size=len(inputs))
            else:               inputs['aug_angle'] = np.linspace(0, 2*np.pi, (self.rot_mult)+1)[rot_index]
            #print('rotating, index = ', rot_index, ' amount = ', inputs['aug_angle'][0])
            self.rotate(inputs)            

            for i, coord in enumerate(self.reflect_axes): inputs[f'aug{coord}'] = int(ref_index[i])
            #print('reflecting, coords = ', self.reflect_axes, ' index = ', ref_index)
            self.reflect(inputs)
            
        elif len(self.reflect_axes) > 0:
            ref_index = self._get_ref_index(aug_index)
            for i, coord in enumerate(self.reflect_axes): inputs[f'aug{coord}'] = int(ref_index[i])
            self.reflect(inputs)
            
        elif self.rot_mult:
            if self.random_rot: inputs['aug_angle'] = 2*np.pi*np.random.random(size=len(inputs))
            else:               inputs['aug_angle'] = np.linspace(0, 2*np.pi, (self.rot_mult)+1)[aug_index]
            self.rotate(inputs)
            
        data['inputs'] = np.nan_to_num(inputs[self.input_feats].values)
        return data
