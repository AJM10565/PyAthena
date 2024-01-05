# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from concurrent.futures import Future
from multiprocessing import cpu_count
from typing import Any, Dict, Iterable, Optional, Tuple, Union, cast

from pyathena import ProgrammingError
from pyathena.async_cursor import AsyncCursor
from pyathena.common import CursorIterator
from pyathena.model import AthenaCompression, AthenaFileFormat, AthenaQueryExecution
from pyathena.pandas.converter import (
    DefaultPandasTypeConverter,
    DefaultPandasUnloadTypeConverter,
)
from pyathena.pandas.result_set import AthenaPandasResultSet

_logger = logging.getLogger(__name__)  # type: ignore


class AsyncPandasCursor(AsyncCursor):
    def __init__(
        self,
        max_workers: int = (cpu_count() or 1) * 5,
        arraysize: int = CursorIterator.DEFAULT_FETCH_SIZE,
        unload: bool = False,
        engine: str = "auto",
        chunksize: Optional[int] = None,
        **kwargs,
    ) -> None:
        super(AsyncPandasCursor, self).__init__(
            max_workers=max_workers, arraysize=arraysize, **kwargs
        )
        self._unload = unload
        self._engine = engine
        self._chunksize = chunksize

    @staticmethod
    def get_default_converter(
        unload: bool = False,
    ) -> Union[DefaultPandasTypeConverter, Any]:
        if unload:
            return DefaultPandasUnloadTypeConverter()
        else:
            return DefaultPandasTypeConverter()

    @property
    def arraysize(self) -> int:
        return self._arraysize

    @arraysize.setter
    def arraysize(self, value: int) -> None:
        if value <= 0:
            raise ProgrammingError("arraysize must be a positive integer value.")
        self._arraysize = value

    def _collect_result_set(
        self,
        query_id: str,
        keep_default_na: bool = False,
        na_values: Optional[Iterable[str]] = ("",),
        quoting: int = 1,
        unload_location: Optional[str] = None,
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> AthenaPandasResultSet:
        if kwargs is None:
            kwargs = dict()
        query_execution = cast(AthenaQueryExecution, self._poll(query_id))
        return AthenaPandasResultSet(
            connection=self._connection,
            converter=self._converter,
            query_execution=query_execution,
            arraysize=self._arraysize,
            retry_config=self._retry_config,
            keep_default_na=keep_default_na,
            na_values=na_values,
            quoting=quoting,
            unload=self._unload,
            unload_location=unload_location,
            engine=kwargs.pop("engine", self._engine),
            chunksize=kwargs.pop("chunksize", self._chunksize),
            **kwargs,
        )

    def execute(
        self,
        operation: str,
        parameters: Optional[Dict[str, Any]] = None,
        work_group: Optional[str] = None,
        s3_staging_dir: Optional[str] = None,
        cache_size: Optional[int] = 0,
        cache_expiration_time: Optional[int] = 0,
        result_reuse_enable: Optional[bool] = None,
        result_reuse_minutes: Optional[int] = None,
        keep_default_na: bool = False,
        na_values: Optional[Iterable[str]] = ("",),
        quoting: int = 1,
        **kwargs,
    ) -> Tuple[str, "Future[Union[AthenaPandasResultSet, Any]]"]:
        if self._unload:
            s3_staging_dir = s3_staging_dir if s3_staging_dir else self._s3_staging_dir
            assert s3_staging_dir, "If the unload option is used, s3_staging_dir is required."
            operation, unload_location = self._formatter.wrap_unload(
                operation,
                s3_staging_dir=s3_staging_dir,
                format_=AthenaFileFormat.FILE_FORMAT_PARQUET,
                compression=AthenaCompression.COMPRESSION_SNAPPY,
            )
        else:
            unload_location = None
        query_id = self._execute(
            operation,
            parameters=parameters,
            work_group=work_group,
            s3_staging_dir=s3_staging_dir,
            cache_size=cache_size,
            cache_expiration_time=cache_expiration_time,
            result_reuse_enable=result_reuse_enable,
            result_reuse_minutes=result_reuse_minutes,
        )
        return (
            query_id,
            self._executor.submit(
                self._collect_result_set,
                query_id,
                keep_default_na,
                na_values,
                quoting,
                unload_location,
                kwargs,
            ),
        )
