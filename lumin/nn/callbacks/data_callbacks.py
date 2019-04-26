from typing import Union, Tuple, Callable, Optional, Dict
import numpy as np

from torch import Tensor

from .callback import Callback
from ..data.batch_yielder import BatchYielder
from ..data.fold_yielder import FoldYielder
from ...utils.misc import to_np, to_device
from ..models.abs_model import AbsModel


class BinaryLabelSmooth(Callback):
    '''Apply label smoothing to binary classes, based on arXiv:1512.00567'''
    def __init__(self, coefs:Union[float,Tuple[float,float]]=0, model:Optional[AbsModel]=None):
        super().__init__(model=model)
        self.coefs = coefs if isinstance(coefs, tuple) else (coefs, coefs)
    
    def on_epoch_begin(self, batch_yielder:BatchYielder, **kargs) -> None:
        '''Apply smoothing at train-time'''
        batch_yielder.targets = batch_yielder.targets.astype(float)
        batch_yielder.targets[batch_yielder.targets == 0] = self.coefs[0]
        batch_yielder.targets[batch_yielder.targets == 1] = 1-self.coefs[1]
         
    def on_eval_begin(self, targets:Tensor, **kargs) -> None:
        '''Apply smoothing at test-time'''
        targets[targets == 0] = self.coefs[0]
        targets[targets == 1] = 1-self.coefs[1]


class DynamicReweight(Callback):
    def __init__(self, reweight:Callable[[Tensor], Tensor], scale:float=1e-1, eval_all_folds:bool=False, model:Optional[AbsModel]=None):
        super().__init__(model=model)
        self.scale,self.reweight,self.eval_all_folds = scale,reweight,eval_all_folds

    def reweight_fold(self, fy:FoldYielder, fold_id:int) -> None:
        fld = fy.get_fold(fold_id)
        preds = self.model.predict_array(fld['inputs'], as_np=False)
        coefs = to_np(self.reweight(preds, to_device(Tensor(fld['targets']))))
        start_sum = np.sum(fld['weights'])
        fld['weights'] += self.scale*coefs*fld['weights']
        fld['weights'] *= start_sum/np.sum(fld['weights'])
        fy.foldfile[f'fold_{fold_id}/weights'][...] = fld['weights'].squeeze()
    
    def on_train_end(self, fy:FoldYielder, val_id:int, **kargs) -> None:
        if self.eval_all_folds:
            for i in range(fy.n_folds): self.reweight_fold(fy, i)
        else:
            self.reweight_fold(fy, val_id)
