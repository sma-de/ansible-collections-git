

ANSIBLE_METADATA = {
    'metadata_version': '1.0',
    'status': ['preview'],
    'supported_by': 'community'
}


import copy
import collections

from ansible.errors import AnsibleFilterError, AnsibleOptionsError
from ansible.module_utils.six import iteritems, string_types
from ansible.module_utils.common._collections_compat import MutableMapping
from ansible.module_utils._text import to_native

from ansible_collections.smabot.base.plugins.module_utils.plugins.plugin_base import MAGIC_ARGSPECKEY_META
from ansible_collections.smabot.base.plugins.module_utils.plugins.filter_base import FilterBase

from ansible_collections.smabot.base.plugins.module_utils.utils.dicting import \
  merge_dicts, \
  setdefault_none



##
## Converts gitserver user secret store mapping into a format
## fitting upstream hashivault write role
##
## note: currently supported structure to save secrets is:
##
##   -> each user has its own secret path, but all user sub
##      secrets (pw, ssh keys are combined there as keys)
##
## possible alternativest support:
##
##   -> each sub secret gets its own path, (one for the pw, and different ones for each ssh key) [TODO]
##   -> all secrets for all users under one single secret path (TODO)
##
##
class ConvertHashiVaultCfgFilter(FilterBase):

    FILTER_ID = 'to_hashivault_cfg'

    @property
    def argspec(self):
        tmp = super(ConvertHashiVaultCfgFilter, self).argspec

        tmp.update({
          'format': (list(string_types), 'per_user', ['per_user']),
          'write': ([bool], False),
          'secrets': ([collections.abc.Mapping], {}),
        })

        return tmp


    def _handle_format_per_user(self, hcfg, secrets, params, indict):
        write_mode = self.get_taskparam('write')

        secrets_cfg = {}

        spath_tmplate = params['vault_path_template']
        def_sets = params['settings']['defaults']

        def_keys = ['all']

        if write_mode:
            def_keys.append('write')
        else:
            def_keys.append('read')

        def_sets_merged = {}

        for x in def_keys:
            merge_dicts(def_sets_merged, copy.deepcopy(def_sets.get(x, {})))

        for k, v in secrets.items():
            ## build secret path by templating with username
            spath = spath_tmplate.format(username=k)

            if write_mode:
                tmp = {'data': v}
            else:
                tmp = {'data_keys': list(v.keys())}

            tmp['path'] = spath

            ## apply user level defaults
            v = merge_dicts(copy.deepcopy(def_sets_merged), tmp)

            ## apply user specific overwrites (TODO)

            secrets_cfg[k] = v

        topkey = 'get_secrets'

        if write_mode:
            topkey = 'set_secrets'

        secrets_cfg = {
          topkey: {
            'secrets': secrets_cfg,
          }
        }

        if not write_mode:
            secrets_cfg[topkey]['return_layout'] = 'mirror_inputcfg'

        merge_dicts(hcfg, secrets_cfg)


    def run_specific(self, indict):
        if not isinstance(indict, MutableMapping):
            raise AnsibleOptionsError(
               "filter input must be a dictionary, but given value"\
               " '{}' has type '{}'".format(indict, type(indict))
            )

        conv_fmt = self.get_taskparam('format')

        hcfg = indict['config']
        params = indict['parameters']
        secrets = self.get_taskparam('secrets') or indict['secrets']

        tmp = getattr(self, '_handle_format_' + conv_fmt, None)

        if not tmp: 
            raise AnsibleOptionsError(
               "Unsupported conversion format '{}'".format(conv_fmt)
            )

        tmp(hcfg, secrets, params, indict)

        return hcfg



# ---- Ansible filters ----
class FilterModule(object):
    ''' generic dictionary filters '''

    def filters(self):
        res = {}

        tmp = [
          ConvertHashiVaultCfgFilter,
        ]

        for f in tmp:
            res[f.FILTER_ID] = f()

        return res

