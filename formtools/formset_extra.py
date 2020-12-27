from django.core.exceptions import ValidationError
from django.forms import Form
from django.forms.fields import BooleanField, IntegerField
from django.forms.utils import ErrorList
from django.forms.widgets import HiddenInput, NumberInput
from django.utils.functional import cached_property
from django.utils.html import html_safe
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _, ngettext

__all__ = ('BaseFormSet', 'formset_factory', 'all_valid')


@html_safe
class BaseFormSet:
    """
    A collection of instances of the same Form class.
    """
    ordering_widget = NumberInput
    default_error_messages = {
        'missing_management_form': _(
            'ManagementForm data is missing or has been tampered with. Missing fields: '
            '%(field_names)s. You may need to file a bug report if the issue persists.'
        ),
    }

    def __init__(self, data=None, files=None, auto_id='id_%s', prefix=None,
                 initial=None, error_class=ErrorList, form_kwargs=None,
                 error_messages=None):
        self.is_bound = data is not None or files is not None
        self.prefix = prefix or self.get_default_prefix()
        self.auto_id = auto_id
        self.data = data or {}
        self.files = files or {}
        self.initial = initial
        self.form_kwargs = form_kwargs or {}
        self.error_class = error_class
        self._errors = None
        self._non_form_errors = None

        messages = {}
        for cls in reversed(type(self).__mro__):
            messages.update(getattr(cls, 'default_error_messages', {}))
        if error_messages is not None:
            messages.update(error_messages)
        self.error_messages = messages

    def __str__(self):
        return self.as_table()

    def __iter__(self):
        """Yield the forms in the order they should be rendered."""
        return iter(self.forms)

    def __getitem__(self, index):
        """Return the form at the given index, based on the rendering order."""
        return self.forms[index]

    def __len__(self):
        return len(self.forms)

    def __bool__(self):
        """
        Return True since all formsets have a management form which is not
        included in the length.
        """
        return True

    @property
    def form_count(self):
        return len(self.base_forms)

    @cached_property
    def forms(self):
        """Instantiate forms at first property access."""
        # DoS protection is included in total_form_count()
        return [
            self._construct_form(i, **self.get_form_kwargs(i))
            for i in range(self.form_count)
        ]

    def get_form_kwargs(self, index):
        """
        Return additional keyword arguments for each individual formset form.

        index will be None if the form being constructed is a new empty
        form.
        """
        return self.form_kwargs.copy()

    def _construct_form(self, i, **kwargs):
        """Instantiate and return the i-th form instance in a formset."""
        defaults = {
            'auto_id': self.auto_id,
            'prefix': self.add_prefix(i),
            'error_class': self.error_class,
            # Don't render the HTML 'required' attribute as it may cause
            # incorrect validation for extra, optional, and deleted
            # forms in the formset.
            'use_required_attribute': False,
        }
        if self.is_bound:
            defaults['data'] = self.data
            defaults['files'] = self.files
        if self.initial and 'initial' not in kwargs:
            try:
                defaults['initial'] = self.initial[i]
            except IndexError:
                pass
        # Allow extra forms to be empty, unless they're part of
        # the minimum forms.
        # if i >= self.initial_form_count() and i >= self.min_num:
        #     defaults['empty_permitted'] = True
        defaults.update(kwargs)
        form = self.base_forms[i](**defaults)
        # self.add_fields(form, i)
        return form

    @property
    def initial_forms(self):
        """Return a list of all the initial forms in this formset."""
        return self.forms

    @property
    def extra_forms(self):
        """Return a list of all the extra forms in this formset."""
        return []

    def empty_form(self, i):
        form = self.base_forms[i](
            auto_id=self.auto_id,
            prefix=self.add_prefix('__prefix__'),
            empty_permitted=True,
            use_required_attribute=False,
            **self.get_form_kwargs(None)
        )
        self.add_fields(form, None)
        return form

    @property
    def cleaned_data(self):
        """
        Return a list of form.cleaned_data dicts for every form in self.forms.
        """
        if not self.is_valid():
            raise AttributeError("'%s' object has no attribute 'cleaned_data'" % self.__class__.__name__)
        return [form.cleaned_data for form in self.forms]

    @property
    def deleted_forms(self):
        """Return a list of forms that have been marked for deletion."""
        return []

    @classmethod
    def get_default_prefix(cls):
        return 'form'

    @classmethod
    def get_ordering_widget(cls):
        return cls.ordering_widget

    def non_form_errors(self):
        """
        Return an ErrorList of errors that aren't associated with a particular
        form -- i.e., from formset.clean(). Return an empty ErrorList if there
        are none.
        """
        if self._non_form_errors is None:
            self.full_clean()
        return self._non_form_errors

    @property
    def errors(self):
        """Return a list of form.errors for every form in self.forms."""
        if self._errors is None:
            self.full_clean()
        return self._errors

    def total_error_count(self):
        """Return the number of errors across all forms in the formset."""
        return len(self.non_form_errors()) +\
            sum(len(form_errors) for form_errors in self.errors)

    def is_valid(self):
        """Return True if every form in self.forms is valid."""
        if not self.is_bound:
            return False
        # Accessing errors triggers a full clean the first time only.
        self.errors
        # List comprehension ensures is_valid() is called for all forms.
        # Forms due to be deleted shouldn't cause the formset to be invalid.
        forms_valid = all([
            form.is_valid() for form in self.forms
        ])
        return forms_valid and not self.non_form_errors()

    def full_clean(self):
        """
        Clean all of self.data and populate self._errors and
        self._non_form_errors.
        """
        self._errors = []
        self._non_form_errors = self.error_class()
        empty_forms_count = 0

        if not self.is_bound:  # Stop further processing.
            return

        for i, form in enumerate(self.forms):
            # Accessing errors calls full_clean() if necessary.
            # _should_delete_form() requires cleaned_data.
            form_errors = form.errors
            self._errors.append(form_errors)
        try:
            self.clean()
        except ValidationError as e:
            self._non_form_errors = self.error_class(e.error_list)

    def clean(self):
        """
        Hook for doing any extra formset-wide cleaning after Form.clean() has
        been called on every form. Any ValidationError raised by this method
        will not be associated with a particular form; it will be accessible
        via formset.non_form_errors()
        """
        pass

    def has_changed(self):
        """Return True if data in any form differs from initial."""
        return any(form.has_changed() for form in self)

    def add_prefix(self, index):
        return '%s-%s' % (self.prefix, index)

    def is_multipart(self):
        """
        Return True if the formset needs to be multipart, i.e. it
        has FileInput, or False otherwise.
        """
        if self.forms:
            return any(form.is_multipart for form in self.forms)
        else:
            return any(
                self.empty_form(i).is_multipart()
                for i in range(len(self.base_forms))
            )

    @property
    def media(self):
        # All the forms on a FormSet are the same, so you only need to
        # interrogate the first form for media.
        if self.forms:
            return any(form.media for form in self.forms)
        else:
            return any(
                self.empty_form(i).media
                for i in range(len(self.base_forms))
            )

    def as_table(self):
        "Return this formset rendered as HTML <tr>s -- excluding the <table></table>."
        # XXX: there is no semantic division between forms here, there
        # probably should be. It might make sense to render each form as a
        # table row with each field as a td.
        forms = ' '.join(form.as_table() for form in self)
        return mark_safe(forms)

    def as_p(self):
        "Return this formset rendered as HTML <p>s."
        forms = ' '.join(form.as_p() for form in self)
        return mark_safe(forms)

    def as_ul(self):
        "Return this formset rendered as HTML <li>s."
        forms = ' '.join(form.as_ul() for form in self)
        return mark_safe(forms)


def formset_factory(name, base_forms, formset=BaseFormSet):
    """Return a FormSet for the given form class."""
    attrs = {
        'base_forms': base_forms,
    }
    return type(name, (formset,), attrs)


def all_valid(formsets):
    """Validate every formset and return True if all are valid."""
    # List comprehension ensures is_valid() is called for all formsets.
    return all([formset.is_valid() for formset in formsets])
