from __future__ import division
import numpy as np
import os
from six.moves import cPickle as pickle
import glob
import warnings
from fastprogress import progress_bar, master_bar
from pathlib import Path
import timeit
from typing import Dict, Union, Any, List, Optional
from sklearn.pipeline import Pipeline
import h5py

from torch.tensor import Tensor

from ..models.model import Model
from ..models.model_builder import ModelBuilder
from ..data.fold_yielder import FoldYielder


class Ensemble():
    def __init__(self, input_pipe:Pipeline=None, output_pipe:Pipeline=None):
        self.input_pipe,self.output_pipe = input_pipe,output_pipe
        self.models = []
        self.weights = []
        self.size = 0
        
    def add_input_pipe(self, pipe:Pipeline) -> None:
        self.input_pipe = pipe

    def add_output_pipe(self, pipe:Pipeline) -> None:
        self.output_pipe = pipe
    
    @staticmethod
    def load_trained_model(model_id:int, model_builder:ModelBuilder, name:str='train_weights/train_') -> Model: 
        model = Model(model_builder)
        model.load(f'{name}{model_id}.h5')
        return model
    
    @staticmethod
    def _get_weights(value:float, metric:str, weighting='reciprocal') -> float:
        if 'metric'.lower() == 'ams': value = 1/value
        if   weighting == 'reciprocal': return 1/value
        elif weighting == 'uniform':    return 1
        else: raise ValueError("No other weighting currently supported")

    def build_ensemble(self, results:List[Dict[str,float]], size:int, model_builder:ModelBuilder,
                       metric:str='loss', weighting:str='reciprocal',
                       snapshot_args:Dict[str,Any]={},
                       location:Path=Path('train_weights'), verbose:bool=True) -> None:

        cycle_losses     = None if 'cycle_losses'     not in snapshot_args else snapshot_args['cycle_losses']
        n_cycles         = None if 'n_cycles'         not in snapshot_args else snapshot_args['n_cycles']
        load_cycles_only = None if 'load_cycles_only' not in snapshot_args else snapshot_args['load_cycles_only']
        patience         = 2    if 'patience'         not in snapshot_args else snapshot_args['patience']
        weighting_pwr    = 0    if 'weighting_pwr'    not in snapshot_args else snapshot_args['weighting_pwr']    
    
        if (cycle_losses is not None and n_cycles is None) or (cycle_losses is None and n_cycles is not None):
            warnings.warn("Warning: cycle ensembles requested, but not enough information passed")
        if cycle_losses is not None and n_cycles is not None and metric is not 'loss':
            warnings.warn("Warning: Setting ensemble metric to loss")
            metric = 'loss'
        if cycle_losses is not None and n_cycles is not None and weighting is not 'uniform':
            warnings.warn("Warning: Setting model weighting to uniform")
            weighting = 'uniform'
    
        self.models = []
        weights = []
    
        if verbose: print(f"Choosing ensemble by {metric}")
        dtype = [('model', int), ('result', float)]
        values = np.sort(np.array([(i, result[metric]) for i, result in enumerate(results)], dtype=dtype), order=['result'])
    
        for i in progress_bar(range(min([size, len(results)]))):
            if not (load_cycles_only and n_cycles):
                self.models.append(self.load_trained_model(values[i]['model'], model_builder, location/'train_'))
                weights.append(self._get_weights(values[i]['result'], metric, weighting))
                if verbose: print(f"Model {i} is {values[i]['model']} with {metric} = {values[i]['result']}")

            if n_cycles:
                end_cycle = len(cycle_losses[values[i]['model']])-patience
                if load_cycles_only: end_cycle += 1
                for n, c in enumerate(range(end_cycle, max(0, end_cycle-n_cycles), -1)):
                    self.models.append(self.load_trained_model(c, model_builder, location/f'{values[i]["model"]}_cycle_'))
                    weights.append((n+1)**weighting_pwr)
                    if verbose: print(f"Model {i} cycle {c} has {metric} = {cycle_losses[values[i]['model']][c]} and weight {weights[-1]}")
        
        weights = np.array(weights)
        self.weights = weights/weights.sum()
        self.size = len(self.models)
        self.n_out = self.models[0].model[-1][-2].out_features
        self.results = results
    
    @staticmethod
    def save_fold_pred(pred:np.ndarray, fold:int, datafile:h5py.File, pred_name:str='pred') -> None:
        try: datafile.create_dataset(f'{fold}/{pred_name}', shape=pred.shape, dtype='float32')
        except RuntimeError: pass
        datafile[f'{fold}/{pred_name}'][...] = pred
        
    def predict_array(self, in_data:Union[List[np.ndarray], np.ndarray], n_models:Optional[int]=None, parent_bar:Optional[master_bar]=None) -> np.ndarray:
        pred = np.zeros((len(in_data), self.n_out))
        
        n_models = len(self.models) if n_models is None else n_models
        models = self.models[:n_models]
        weights = self.weights[:n_models]
        weights = weights/weights.sum()
        
        for i, m in enumerate(progress_bar(models, parent=parent_bar, display=bool(parent_bar))):
            tmp_pred = m.predict(Tensor(in_data))
            if self.output_pipe is not None: tmp_pred = self.output_pipe.inverse_transform(tmp_pred)
            pred += weights[i]*tmp_pred
        return pred
    
    def fold_predict(self, fold_yielder:FoldYielder, n_models:Optional[int]=None, pred_name:str='pred') -> None:
        n_models = len(self.models) if n_models is None else n_models
        times = []
        mb = master_bar(range(len(fold_yielder.source)))
        for fold_id in mb:
            fold_tmr = timeit.default_timer()
            if not fold_yielder.test_time_aug:
                fold = fold_yielder.get_fold(fold_id)['inputs']
                pred = self.predict_array(fold, n_models, mb)

            else:
                tmpPred = []
                pb = progress_bar(range(fold_yielder.aug_mult), parent=mb)
                for aug in pb:
                    fold = fold_yielder.get_test_fold(fold_id, aug)['inputs']
                    tmpPred.append(self.predict_array(fold, n_models))
                pred = np.mean(tmpPred, axis=0)

            times.append((timeit.default_timer()-fold_tmr)/len(fold))
            if self.n_out > 1: self.save_fold_pred(pred, f'fold_{fold_id}', fold_yielder.source, pred_name=pred_name)
            else: self.save_fold_pred(pred[:, 0], f'fold_{fold_id}', fold_yielder.source, pred_name=pred_name)
        print(f'Mean time per event = {np.mean(times):.4E}±{np.std(times, ddof=1)/np.sqrt(len(times)):.4E}')

    def predict(self, in_data:Union[np.ndarray, FoldYielder, List[np.ndarray]], n_models:Optional[int]=None, pred_name:str='pred') -> Union[None, np.ndarray]:
        if not isinstance(in_data, FoldYielder): return self.predict_array(in_data, n_models)
        self.fold_predict(in_data, n_models, pred_name)
    
    def save(self, name:str, feats:List[str]=None, overwrite:bool=False) -> None:
        if (len(glob.glob(f"{name}*.json")) or len(glob.glob(f"{name}*.h5")) or len(glob.glob(f"{name}*.pkl"))) and not overwrite:
            raise FileExistsError("Ensemble already exists with that name, call with overwrite=True to force save")
        else:
            os.makedirs(name, exist_ok=True)
            os.system(f"rm {name}*.json {name}*.h5 {name}*.pkl")
            for i, model in enumerate(progress_bar(self.models)): model.save(f'{name}_{i}.h5')    
            with open(f'{name}_weights.pkl', 'wb')         as fout: pickle.dump(self.weights, fout)
            with open(f'{name}_results.pkl', 'wb')         as fout: pickle.dump(self.results, fout)
            if self.input_pipe  is not None: 
                with open(f'{name}_input_pipe.pkl', 'wb')  as fout: pickle.dump(self.input_pipe, fout)
            if self.output_pipe is not None: 
                with open(f'{name}_output_pipe.pkl', 'wb') as fout: pickle.dump(self.output_pipe, fout)
            if feats            is not None: 
                with open(f'{name}_feats.pkl', 'wb')       as fout: pickle.dump(feats, fout)
                    
    def load(self, name:str, model_builder) -> None:
        names = glob.glob(f'{name}_*.h5')
        self.models = []
        for n in progress_bar(sorted(names)):
            m = Model(model_builder)
            m.load(n)
            self.models.append(m)
        self.n_out = self.models[0].model[-1][-2].out_features
        with     open(f'{name}_weights.pkl', 'rb')     as fin: self.weights     = pickle.load(fin)
        try: 
            with open(f'{name}_input_pipe.pkl', 'rb')  as fin: self.input_pipe  = pickle.load(fin)
        except FileNotFoundError: pass
        try: 
            with open(f'{name}_output_pipe.pkl', 'rb') as fin: self.output_pipe = pickle.load(fin)
        except FileNotFoundError: pass
        try: 
            with open(f'{name}_feats.pkl', 'rb')       as fin: self.feats       = pickle.load(fin)
        except FileNotFoundError: pass
