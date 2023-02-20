#!/usr/bin/env sh

mkdir upper lower work

ls

mount -t overlay overlay -o userxattr,lowerdir="${PWD}/lower,upperdir=${PWD}/upper,workdir=${PWD}/work" mnt


#tar --xattrs-include='user.overlay.*'
