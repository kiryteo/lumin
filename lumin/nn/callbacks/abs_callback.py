from abc import ABC


class AbsCallback(ABC):
    '''Abstract callback class for typing'''
    def __init__(self): pass
    def set_model(self): pass
    def set_plot_settings(self): pass
    def on_train_begin(self, **kargs): pass  
    def on_train_end(self,   **kargs): pass  
    def on_epoch_begin(self, **kargs): pass
    def on_epoch_end(self,   **kargs): pass
    def on_batch_begin(self, **kargs): pass
    def on_batch_end(self,   **kargs): pass
