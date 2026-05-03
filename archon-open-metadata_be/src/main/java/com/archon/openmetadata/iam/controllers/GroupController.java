package com.archon.openmetadata.iam.controllers;

import com.archon.openmetadata.iam.dto.GroupDto;
import com.archon.openmetadata.iam.dto.GroupFilterBean;
import com.archon.openmetadata.iam.models.Group;
import com.archon.openmetadata.iam.services.GroupService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.persistence.criteria.Predicate;
import lombok.RequiredArgsConstructor;
import org.modelmapper.ModelMapper;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.data.web.PagedResourcesAssembler;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.PagedModel;
import org.springframework.hateoas.server.mvc.WebMvcLinkBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/groups")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class GroupController {

  private final GroupService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<Group> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<GroupDto>>> fetchAll(Pageable pageable) {
    Page<Group> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, GroupDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(GroupController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<GroupDto>> getById(@PathVariable UUID id) {
    Group entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, GroupDto.class),
            WebMvcLinkBuilder.linkTo(WebMvcLinkBuilder.methodOn(GroupController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<GroupDto>> create(@RequestBody GroupDto dto) {
    Group saved = service.save(modelMapper.map(dto, Group.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, GroupDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(GroupController.class).getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<GroupDto>> update(
      @PathVariable UUID id, @RequestBody GroupDto dto) {
    dto.setId(id);
    Group updated = service.save(modelMapper.map(dto, Group.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, GroupDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(GroupController.class).getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<GroupDto>>> searchAll(
      @RequestBody GroupFilterBean filterBean, Pageable pageable) {
    Page<Group> page =
        service.findAll(
            (root, query, criteriaBuilder) -> {
              List<Predicate> predicates = new ArrayList<>();

              // Example of inline explicit criteria matching your requirement
              if (filterBean.getSearchText() != null && !filterBean.getSearchText().isEmpty()) {
                // Replace "id" with actual string fields you want to search
                predicates.add(
                    criteriaBuilder.like(
                        root.get("id").as(String.class), "%" + filterBean.getSearchText() + "%"));
              }

              if (predicates.isEmpty()) {
                return criteriaBuilder.conjunction();
              }
              return criteriaBuilder.and(predicates.toArray(new Predicate[0]));
            },
            pageable);

    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, GroupDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(GroupController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }
}
