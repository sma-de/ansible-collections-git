
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


import collections

##from ansible.errors import AnsibleOptionsError, AnsibleModuleError##, AnsibleError
####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.gitlab.plugins.module_utils.plugins.gitlab_action import GitlabBase
from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert


class ActionModule(GitlabBase):

    def __init__(self, *args, **kwargs):
        super(ActionModule, self).__init__(*args, **kwargs)

        self._supports_check_mode = False
        self._supports_async = False


    @property
    def argspec(self):
        tmp = super(ActionModule, self).argspec

        tmp.update({
          'modname': (list(string_types)),
          'modargs': ([collections.abc.Mapping], {}),
        })

        return tmp


    def run_specific(self, result):
        cmdret = self.exec_gitlab_module(
           self.get_taskparam('modname'), 
           modargs=self.get_taskparam('modargs')
        )

        result.update(cmdret)
        return result

