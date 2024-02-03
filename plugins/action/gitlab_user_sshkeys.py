
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


import collections

##from ansible.errors import AnsibleOptionsError, AnsibleModuleError##, AnsibleError
####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.git.plugins.module_utils.plugins.gitlab_action import GitlabUserBase
from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert

from ansible_collections.smabot.base.plugins.module_utils.utils.dicting import \
  setdefault_none

from ansible.utils.display import Display


display = Display()


class ActionModule(GitlabUserBase):

    def __init__(self, *args, **kwargs):
        super(ActionModule, self).__init__(*args, **kwargs)

        self._supports_check_mode = False
        self._supports_async = False


    @property
    def argspec(self):
        tmp = super(ActionModule, self).argspec

        tmp.update({
          'keys': ([collections.abc.Mapping]),

          'exclusive': ([bool], False),
          'state': (list(string_types), 'present', ['present', 'absent']),
        })

        return tmp


    def _add_key_to_outputmap(self, keymap, keys_state, submap,
        extra_output=None
    ):
        res = {}

        ## note: we will add a specific subset of keys to output map
        for x in ['title', 'pubkey']:
            res[x] = keymap[x]

        if extra_output:
            res.update(extra_output)

        keys_state[submap][keymap['mapkey']] = res


    def _handle_state_present(self, result, keymap, keys_state):
        usr = self.gitlab_user

        # check if sshkey already exists
        sshkey = None
        keytitle = keymap['title']
        keycontent = keymap['pubkey']

        keys_state['to_keep'].append(keymap['title'])

        display.vv(
          "ActionModule[_handle_state_present] :: check if ssh key already"\
          " exists with given title '{}' ...".format(keytitle)
        )

        for k in usr.keys.list():
            if k.title == keytitle:
                display.vv(
                  "ActionModule[_handle_state_present] :: found existing key"
                )

                sshkey = k

        outmap = 'new'

        if sshkey:
            ##
            ## we cannot do a simple string compare here because gitlab
            ## automagically adds a default comment to keys which do
            ## not have a comment set
            ##
            old_key = sshkey.key.split(' ')
            new_key = keycontent.split(' ')

            if old_key[0:2] == new_key[0:2]:
                ## key itself and type prefix are identical,
                ## check comment now
                keys_equals = False

                if len(new_key) == 2:
                    ##
                    ## new keys has no comment, either old key has no
                    ## comment too, or has gitlab default comment,
                    ## in both cases keys should be considered equal
                    ##
                    ## NOTE: this behaves wrong in maybe somewhat
                    ##   obscure edge cases where someone only wants
                    ##   to update the key comment while the rest
                    ##   stays the same, if this really becomes a
                    ##   practical issues someday do something like
                    ##   adding a force-update state or so
                    ##
                    keys_equals = True
                elif len(old_key) == 2:
                    ##
                    ## old key has no comment, but new one has, so keys
                    ## are not equal and a key update should be performed
                    ##
                    keys_equals = False
                elif new_key[-1] == old_key[-1]:
                    ##
                    ## both keys have a comment set which is identical,
                    ## so both keys are equal again
                    ##
                    keys_equals = True

                if keys_equals:
                    # nothing changed, so nothing todo
                    self._add_key_to_outputmap(keymap, keys_state, 'unchanged')
                    return result

            display.vv(
               "ActionModule[_handle_state_present] ::"\
               " new and old key differ, update key"
            )

            display.vvv(
               "ActionModule[_handle_state_present] ::"\
               " old pubkey: |{}|".format(sshkey.key)
            )

            display.vvv(
               "ActionModule[_handle_state_present] ::"\
               " new pubkey: |{}|".format(keycontent)
            )

            # key already exists, but it changed, as gitlab API 
            # seems not to support direct update, we will delete 
            # it first and than readd it
            sshkey.delete()
            outmap = 'updated'

        # if we get to this point, we have either a completly new 
        # key or an already existing one which has changed, in 
        # any case we need to add the key
        display.vv(
          "ActionModule[_handle_state_present] ::"\
          " add new key: {}".format(keycontent)
        )

        usr.keys.create({
            'title': keytitle,
            'key': keycontent,
        })

        self._add_key_to_outputmap(keymap, keys_state, outmap)
        result['changed'] = True


    def _handle_state_absent(self, result, keymap, keys_state):
        usr = self.gitlab_user

        # check if sshkey already exists
        keytitle = v['title']

        display.vv(
          "ActionModule[_handle_state_absent] :: check if a ssh key"\
          " with given title exists '{}' ...".format(keytitle)
        )

        for k in usr.keys.list():
            if k.title != keytitle:
                continue

            display.vv(
              "ActionModule[_handle_state_absent] ::"\
              " key with such a title exists, remove it ..."
            )

            k.delete()

            self._add_key_to_outputmap(
               keymap, keys_state, 'removed',
               extra_output={'reason': 'by absent setting'}
            )

            result['changed'] = True


    def run_specific(self, result):
        keys_state = {
          'new': {},
          'updated': {},
          'removed': {},
          'unchanged': {},
          'to_keep': [],
        }

        for k, v in self.get_taskparam('keys').items():
            if not isinstance(v, collections.abc.Mapping):
                if not v:
                    ## simple form "negative value" ==> ensure key with this title is absent
                    v = {'state': 'absent'}
                else:
                    ## simple form "positive value" ==> assume string representing pubkey to add
                    v = {'pubkey': v}

            v['mapkey'] = k

            ## default title to dict mapping key
            setdefault_none(v, 'title', k)

            ## default state to modargs common state
            setdefault_none(v, 'state', self.get_taskparam('state'))

            tmp = getattr(self, '_handle_state_' + v['state'], None)

            ansible_assert(tmp,
               "internal error: failed to find method handling"\
               " state '{}'".format(v['state'])
            )

            tmp(result, v, keys_state)

        ##usr.save()

        to_keep = keys_state.pop('to_keep')

        if self.get_taskparam('exclusive'):
            ## exclusive mode, remove any ssh keys not explicitly
            ## part of "current present group"
            for k in usr.keys.list():
                if k.title in to_keep:
                    continue

                display.vv(
                  "ActionModule[run_specific] :: remove key"\
                  " with title '{}' because of activated"\
                  " exclusive mode ...".format(k.title)
                )

                k.delete()

                self._add_key_to_outputmap(
                   keymap, keys_state, 'removed',
                   extra_output={'reason': 'by exclusive mode'}
                )

                result['changed'] = True

        result['keys'] = keys_state
        return result

